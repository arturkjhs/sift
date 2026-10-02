"""In-memory chat state, kept up to date from TDLib updates.

Qt-free on purpose: Qt models in tgclient.models subscribe via ChatStore.subscribe().

TDLib JSON notes:
- int64 fields (e.g. chatPosition.order) arrive as strings; int53 fields (chat ids) as numbers.
- A chat belongs to a chat list iff it has a position with non-zero order in that list.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ..td.client import Event, TdClient, TdError
from .files import FileManager

log = logging.getLogger(__name__)

MAIN = "main"
ARCHIVE = "archive"

ChangeKind = Literal["chat", "folders", "unread", "unread_messages"]
Listener = Callable[[ChangeKind, Any], None]

_SCOPE_BY_TYPE = {
    "private": "notificationSettingsScopePrivateChats",
    "secret": "notificationSettingsScopePrivateChats",
    "group": "notificationSettingsScopeGroupChats",
    "supergroup": "notificationSettingsScopeGroupChats",
    "channel": "notificationSettingsScopeChannelChats",
}


def list_key(chat_list: dict[str, Any]) -> str:
    match chat_list["@type"]:
        case "chatListMain":
            return MAIN
        case "chatListArchive":
            return ARCHIVE
        case "chatListFolder":
            return f"folder:{chat_list['chat_folder_id']}"
        case other:
            return other


def chat_list_obj(key: str) -> dict[str, Any]:
    if key == MAIN:
        return {"@type": "chatListMain"}
    if key == ARCHIVE:
        return {"@type": "chatListArchive"}
    if key.startswith("folder:"):
        return {"@type": "chatListFolder", "chat_folder_id": int(key.split(":", 1)[1])}
    raise ValueError(f"Unknown chat list key: {key}")


@dataclass
class Position:
    order: int
    is_pinned: bool


@dataclass
class Chat:
    id: int
    title: str
    type: str  # private | secret | group | supergroup | channel
    unread_count: int = 0
    unread_mention_count: int = 0
    positions: dict[str, Position] = field(default_factory=dict)
    last_message: dict[str, Any] | None = None
    photo_file_id: int | None = None
    photo_path: str | None = None
    mute_for: int = 0
    use_default_mute_for: bool = True
    last_read_outbox_message_id: int = 0
    last_read_inbox_message_id: int = 0
    peer_id: int = 0  # user id (private, secret), basic group id or supergroup id
    draft: dict[str, Any] | None = None  # TDLib draftMessage


@dataclass
class Folder:
    key: str
    title: str


class ChatStore:
    """Create before the client's first request: TDLib sends updateNewChat only once per chat."""

    def __init__(self, client: TdClient, files: FileManager | None = None) -> None:
        self._client = client
        self.files = files or FileManager(client)
        self.chats: dict[int, Chat] = {}
        # Display order, including the main list. Replaced on updateChatFolders.
        self.folders: list[Folder] = [Folder(key=MAIN, title="All chats")]
        self.unread: dict[str, int] = {}  # list key -> unread unmuted chats
        self.unread_messages: dict[str, int] = {}  # list key -> unread unmuted messages
        self._scope_mute_for: dict[str, int] = {}
        self._file_owners: dict[int, set[int]] = {}  # file id -> chat ids using it as photo
        self._loading: set[str] = set()
        self._fully_loaded: set[str] = set()
        self._listeners: list[Listener] = []

        handlers: dict[str, Callable[[Event], None]] = {
            "updateNewChat": self._on_new_chat,
            "updateChatTitle": self._on_title,
            "updateChatPhoto": self._on_photo,
            "updateChatLastMessage": self._on_last_message,
            "updateChatPosition": self._on_position,
            "updateChatDraftMessage": self._on_draft,
            "updateChatReadInbox": self._on_read_inbox,
            "updateChatReadOutbox": self._on_read_outbox,
            "updateChatUnreadMentionCount": self._on_mention_count,
            # Reading a message with a mention sends this one, not the update above.
            "updateMessageMentionRead": self._on_mention_count,
            "updateChatNotificationSettings": self._on_notification_settings,
            "updateScopeNotificationSettings": self._on_scope_settings,
            "updateChatFolders": self._on_folders,
            "updateUnreadChatCount": self._on_unread_chat_count,
            "updateUnreadMessageCount": self._on_unread_message_count,
        }
        for update_type, handler in handlers.items():
            client.on(update_type, handler)
        self.files.subscribe(self._on_file)

    # --- public API -------------------------------------------------------------------------

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def chats_in(self, key: str) -> list[Chat]:
        chats = [c for c in self.chats.values() if key in c.positions]
        chats.sort(key=lambda c: (c.positions[key].order, c.id), reverse=True)
        return chats

    def is_muted(self, chat: Chat) -> bool:
        if chat.use_default_mute_for:
            scope = _SCOPE_BY_TYPE.get(chat.type)
            return self._scope_mute_for.get(scope or "", 0) > 0
        return chat.mute_for > 0

    def is_fully_loaded(self, key: str) -> bool:
        return key in self._fully_loaded

    def file_path(self, file_id: int) -> str | None:
        return self.files.path(file_id)

    async def load_more(self, key: str, limit: int = 50) -> None:
        """Ask TDLib for more chats of a list. Chats arrive as position updates."""
        if key in self._loading or key in self._fully_loaded:
            return
        self._loading.add(key)
        try:
            await self._client.send(
                {"@type": "loadChats", "chat_list": chat_list_obj(key), "limit": limit}
            )
        except TdError as e:
            if e.code == 404:
                self._fully_loaded.add(key)
            else:
                log.warning("loadChats(%s) failed: %s", key, e)
        finally:
            self._loading.discard(key)

    def request_photo(self, chat_id: int) -> None:
        """Start downloading a chat's small photo if needed. Safe to call repeatedly."""
        chat = self.chats.get(chat_id)
        if chat is not None and chat.photo_file_id is not None and not chat.photo_path:
            self.files.download(chat.photo_file_id)

    # --- internals --------------------------------------------------------------------------

    def _emit(self, kind: ChangeKind, payload: Any = None) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:
                log.exception("Store listener failed")

    def _set_photo(self, chat: Chat, photo: dict[str, Any] | None) -> None:
        if chat.photo_file_id is not None:
            owners = self._file_owners.get(chat.photo_file_id)
            if owners:
                owners.discard(chat.id)
        chat.photo_file_id = None
        chat.photo_path = None
        if not photo:
            return
        small = photo["small"]
        chat.photo_file_id = small["id"]
        self._file_owners.setdefault(small["id"], set()).add(chat.id)
        self.files.register(small)
        chat.photo_path = self.files.path(small["id"])

    def _on_file(self, file_id: int) -> None:
        path = self.files.path(file_id)
        if not path:
            return
        for chat_id in self._file_owners.get(file_id, ()):
            chat = self.chats.get(chat_id)
            if chat is not None and chat.photo_path != path:
                chat.photo_path = path
                self._emit("chat", chat_id)

    def _apply_positions(self, chat: Chat, positions: list[dict[str, Any]]) -> None:
        for position in positions:
            self._apply_position(chat, position)

    @staticmethod
    def _apply_position(chat: Chat, position: dict[str, Any]) -> None:
        key = list_key(position["list"])
        order = int(position["order"])
        if order == 0:
            chat.positions.pop(key, None)
        else:
            chat.positions[key] = Position(order=order, is_pinned=position.get("is_pinned", False))

    def _apply_notification_settings(self, chat: Chat, settings: dict[str, Any]) -> None:
        chat.mute_for = settings.get("mute_for", 0)
        chat.use_default_mute_for = settings.get("use_default_mute_for", True)

    def _on_new_chat(self, event: Event) -> None:
        raw = event["chat"]
        chat = Chat(
            id=raw["id"],
            title=raw.get("title", ""),
            type=_chat_type(raw["type"]),
            unread_count=raw.get("unread_count", 0),
            unread_mention_count=raw.get("unread_mention_count", 0),
            last_message=raw.get("last_message"),
            last_read_outbox_message_id=raw.get("last_read_outbox_message_id", 0),
            last_read_inbox_message_id=raw.get("last_read_inbox_message_id", 0),
            peer_id=_peer_id(raw["type"]),
            draft=raw.get("draft_message"),
        )
        self.chats[chat.id] = chat
        self._set_photo(chat, raw.get("photo"))
        self._apply_positions(chat, raw.get("positions", []))
        self._apply_notification_settings(chat, raw.get("notification_settings", {}))
        self._emit("chat", chat.id)

    def _update_chat(self, chat_id: int, apply: Callable[[Chat], None]) -> None:
        chat = self.chats.get(chat_id)
        if chat is None:
            return
        apply(chat)
        self._emit("chat", chat_id)

    def _on_title(self, event: Event) -> None:
        self._update_chat(event["chat_id"], lambda c: setattr(c, "title", event["title"]))

    def _on_photo(self, event: Event) -> None:
        self._update_chat(event["chat_id"], lambda c: self._set_photo(c, event.get("photo")))

    def _on_last_message(self, event: Event) -> None:
        def apply(chat: Chat) -> None:
            chat.last_message = event.get("last_message")
            self._apply_positions(chat, event.get("positions", []))

        self._update_chat(event["chat_id"], apply)

    def _on_position(self, event: Event) -> None:
        self._update_chat(event["chat_id"], lambda c: self._apply_position(c, event["position"]))

    def _on_draft(self, event: Event) -> None:
        def apply(chat: Chat) -> None:
            chat.draft = event.get("draft_message")
            self._apply_positions(chat, event.get("positions", []))

        self._update_chat(event["chat_id"], apply)

    def _on_read_inbox(self, event: Event) -> None:
        def apply(chat: Chat) -> None:
            chat.unread_count = event["unread_count"]
            chat.last_read_inbox_message_id = event.get(
                "last_read_inbox_message_id", chat.last_read_inbox_message_id)

        self._update_chat(event["chat_id"], apply)

    def _on_read_outbox(self, event: Event) -> None:
        self._update_chat(
            event["chat_id"],
            lambda c: setattr(
                c, "last_read_outbox_message_id", event["last_read_outbox_message_id"]
            ),
        )

    def _on_mention_count(self, event: Event) -> None:
        self._update_chat(
            event["chat_id"],
            lambda c: setattr(c, "unread_mention_count", event["unread_mention_count"]),
        )

    def _on_notification_settings(self, event: Event) -> None:
        self._update_chat(
            event["chat_id"],
            lambda c: self._apply_notification_settings(c, event["notification_settings"]),
        )

    def _on_scope_settings(self, event: Event) -> None:
        scope = event["scope"]["@type"]
        self._scope_mute_for[scope] = event["notification_settings"].get("mute_for", 0)
        for chat in self.chats.values():
            if chat.use_default_mute_for and _SCOPE_BY_TYPE.get(chat.type) == scope:
                self._emit("chat", chat.id)

    def _on_folders(self, event: Event) -> None:
        folders = [
            Folder(key=f"folder:{info['id']}", title=_folder_title(info))
            for info in event.get("chat_folders", [])
        ]
        main_index = min(event.get("main_chat_list_position", 0), len(folders))
        folders.insert(main_index, Folder(key=MAIN, title="All chats"))
        self.folders = folders
        self._emit("folders")

    def _on_unread_chat_count(self, event: Event) -> None:
        key = list_key(event["chat_list"])
        self.unread[key] = event.get("unread_unmuted_count", 0)
        self._emit("unread", key)

    def _on_unread_message_count(self, event: Event) -> None:
        key = list_key(event["chat_list"])
        self.unread_messages[key] = event.get("unread_unmuted_count", 0)
        self._emit("unread_messages", key)


def _folder_title(info: dict[str, Any]) -> str:
    name = info.get("name")
    if isinstance(name, dict):  # newer TDLib: chatFolderName { text: formattedText }
        return name.get("text", {}).get("text", "")
    return info.get("title", "")


def _peer_id(type_obj: dict[str, Any]) -> int:
    return int(type_obj.get("user_id") or type_obj.get("basic_group_id")
               or type_obj.get("supergroup_id") or 0)


def draft_text(draft: dict[str, Any] | None) -> str:
    """Plain text of a TDLib draftMessage (any TDLib version), "" if none."""
    if not draft:
        return ""
    content = draft.get("content") or draft.get("input_message_text") or {}
    text = content.get("text")
    return (text.get("text", "") if isinstance(text, dict) else text or "")


def draft_reply_to(draft: dict[str, Any] | None) -> int:
    reply_to = (draft or {}).get("reply_to") or {}
    if reply_to.get("@type") == "inputMessageReplyToMessage":
        return int(reply_to.get("message_id") or 0)
    return int((draft or {}).get("reply_to_message_id") or 0)  # older TDLib


def _chat_type(type_obj: dict[str, Any]) -> str:
    match type_obj["@type"]:
        case "chatTypePrivate":
            return "private"
        case "chatTypeBasicGroup":
            return "group"
        case "chatTypeSupergroup":
            return "channel" if type_obj.get("is_channel") else "supergroup"
        case "chatTypeSecret":
            return "secret"
        case other:
            return other
