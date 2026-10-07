"""The profile panel: a chat's or a person's info, members, groups in common and what was
shared (media, files, links, voice messages). Exposed to QML as `profile`."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..store.chats import ChatStore
from ..store.files import AUTO_PRIORITY
from ..store.format import content_preview, initials, short_time
from ..store.link_preview import message_link_preview
from ..store.media import extract, human_size
from ..store.presence import PresenceStore, members_text, status_text
from ..store.richtext import utf16_index_map
from ..store.users import UserStore
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

AVATAR_COLORS = 7
PAGE = 50
TABS = {
    "media": "searchMessagesFilterPhotoAndVideo",
    "files": "searchMessagesFilterDocument",
    "links": "searchMessagesFilterUrl",
    "voice": "searchMessagesFilterVoiceNote",
}


class ProfileModel(QObject):
    changed = Signal()
    sharedChanged = Signal()

    def __init__(self, client: TdClient, chats: ChatStore, users: UserStore,
                 presence: PresenceStore | None = None, parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._chats = chats
        self._users = users
        self._presence = presence
        self._chat_id = 0  # the chat whose profile it is (0: a user without a chat yet)
        self._user_id = 0  # a person's profile
        self._info: dict[str, Any] = {}
        self._members: list[dict[str, Any]] = []
        self._common: list[dict[str, Any]] = []
        self._tab = "media"
        self._shared: list[dict[str, Any]] = []
        self._shared_next = 0
        self._shared_busy = False
        self._generation = 0  # bumps on every open(): late answers are dropped
        # file id -> rows (and the field) showing it once downloaded, with the URL kind
        self._pending: dict[int, list[tuple[dict[str, Any], str, str]]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        chats.files.subscribe(self._on_file)
        chats.subscribe(self._on_chats)
        users.subscribe(lambda user_id: self.changed.emit()
                        if user_id and user_id == self._user_id else None)

    # --- QML API ----------------------------------------------------------------------------

    @Slot("QVariant", "QVariant")
    def open(self, chat_id: Any, user_id: Any = 0) -> None:
        """A chat's profile, or a person's (user_id; chat_id: their private chat if known)."""
        self._chat_id, self._user_id = int(chat_id or 0), int(user_id or 0)
        chat = self._chats.chats.get(self._chat_id)
        if not self._user_id and chat is not None and chat.type in ("private", "secret"):
            self._user_id = chat.peer_id
        if self._user_id and not self._chat_id and self._user_id in self._chats.chats:
            self._chat_id = self._user_id  # a private chat's id is the user's id
        self._generation += 1
        self._info, self._members, self._common = {}, [], []
        self._shared, self._shared_next = [], 0
        self._pending = {}
        self.changed.emit()
        self.sharedChanged.emit()
        self._spawn(self._load(self._generation))
        self.setTab(self._tab, force=True)

    @Property(bool, notify=changed)
    def isUser(self) -> bool:
        return bool(self._user_id)

    @Property("QVariant", notify=changed)
    def chatId(self) -> int:
        return self._chat_id

    @Property("QVariant", notify=changed)
    def userId(self) -> int:
        return self._user_id

    @Property(str, notify=changed)
    def title(self) -> str:
        if self._user_id:
            user = self._users.users.get(self._user_id)
            return user.full_name if user else ""
        chat = self._chats.chats.get(self._chat_id)
        return chat.title if chat else ""

    @Property(str, notify=changed)
    def subtitle(self) -> str:
        """last seen / members."""
        if self._presence is None:
            return ""
        if self._user_id:
            if self._user_id in self._presence.bots:
                return "bot"
            return status_text(self._presence.statuses.get(self._user_id))
        chat = self._chats.chats.get(self._chat_id)
        if chat is None:
            return ""
        count = self._info.get("member_count") or self._presence.members.get(chat.peer_id, 0)
        return members_text(count, 0, chat.type == "channel")

    @Property(str, notify=changed)
    def avatar(self) -> str:
        file_id = self._photo_file()
        if file_id is None:
            return ""
        files = self._chats.files
        if files.path(file_id):
            return files.url("avatar", file_id)
        files.download(file_id, AUTO_PRIORITY)
        return ""

    @Property(str, notify=changed)
    def initials(self) -> str:
        return initials(self.title)

    @Property(int, notify=changed)
    def colorIndex(self) -> int:
        return abs(self._user_id or self._chat_id) % AVATAR_COLORS

    @Property(bool, notify=changed)
    def isContact(self) -> bool:
        user = self._users.users.get(self._user_id) if self._user_id else None
        return bool(user and (user.is_contact or user.id == self._users.my_id))

    @Property(bool, notify=changed)
    def muted(self) -> bool:
        chat = self._chats.chats.get(self._chat_id)
        return chat is not None and self._chats.is_muted(chat)

    @Property(str, notify=changed)
    def description(self) -> str:
        """Bio of a person, description of a group or channel."""
        return str(self._info.get("description", ""))

    @Property(str, notify=changed)
    def username(self) -> str:
        if self._user_id:
            user = self._users.users.get(self._user_id)
            return user.usernames[0] if user and user.usernames else ""
        chat = self._chats.chats.get(self._chat_id)
        if chat is None or self._presence is None:
            return ""
        return self._presence.group_usernames.get(chat.peer_id, "")

    @Property(str, notify=changed)
    def phone(self) -> str:
        user = self._users.users.get(self._user_id) if self._user_id else None
        return f"+{user.phone}" if user and user.phone else ""

    @Property("QVariantList", notify=changed)
    def members(self) -> list[dict[str, Any]]:
        return list(self._members)

    @Property("QVariantList", notify=changed)
    def commonGroups(self) -> list[dict[str, Any]]:
        return list(self._common)

    @Property(str, notify=sharedChanged)
    def tab(self) -> str:
        return self._tab

    @Property("QVariantList", notify=sharedChanged)
    def shared(self) -> list[dict[str, Any]]:
        """Items of the current tab: {messageId, chatId, kind, image, title, subtitle, url}."""
        return list(self._shared)

    @Property(bool, notify=sharedChanged)
    def sharedBusy(self) -> bool:
        return self._shared_busy

    @Slot(str)
    def setTab(self, tab: str, force: bool = False) -> None:
        if tab not in TABS or (tab == self._tab and not force):
            return
        self._tab = tab
        self._shared, self._shared_next = [], 0
        self.sharedChanged.emit()
        if self._chat_id:
            self._spawn(self._load_shared(self._generation, tab, 0))

    @Slot()
    def loadMoreShared(self) -> None:
        if self._shared_next and not self._shared_busy and self._chat_id:
            self._spawn(self._load_shared(self._generation, self._tab, self._shared_next))

    # --- loading ----------------------------------------------------------------------------

    async def _load(self, generation: int) -> None:
        chat = self._chats.chats.get(self._chat_id)
        try:
            if self._user_id:
                full = await self._client.send({"@type": "getUserFullInfo",
                                                "user_id": self._user_id})
                bio = full.get("bio") or {}
                about = (full.get("bot_info") or {}).get("short_description", "")
                info = {"description": (bio.get("text", "") if isinstance(bio, dict) else bio)
                        or about}
                if full.get("group_in_common_count"):
                    common = await self._client.send({"@type": "getGroupsInCommon",
                                                      "user_id": self._user_id,
                                                      "offset_chat_id": 0, "limit": 20})
                    if generation == self._generation:
                        self._common = [self._chat_row(i) for i in common.get("chat_ids") or []
                                        if i in self._chats.chats]
            elif chat is not None and chat.type in ("supergroup", "channel"):
                full = await self._client.send({"@type": "getSupergroupFullInfo",
                                                "supergroup_id": chat.peer_id})
                info = {"description": full.get("description", ""),
                        "member_count": full.get("member_count", 0)}
                if full.get("can_get_members", chat.type == "supergroup") and (
                        chat.type == "supergroup"):
                    members = await self._client.send({
                        "@type": "searchChatMembers", "chat_id": chat.id, "query": "",
                        "limit": 200, "filter": None})
                    if generation == self._generation:
                        self._members = self._member_rows(members.get("members") or [])
            elif chat is not None and chat.type == "group":
                full = await self._client.send({"@type": "getBasicGroupFullInfo",
                                                "basic_group_id": chat.peer_id})
                info = {"description": full.get("description", "")}
                self._members = self._member_rows(full.get("members") or [])
            else:
                info = {}
        except TdError as e:
            log.info("Profile info failed: %s", e)
            info = {}
        if generation == self._generation:
            self._info = info
            self.changed.emit()

    async def _load_shared(self, generation: int, tab: str, from_id: int) -> None:
        self._shared_busy = True
        self.sharedChanged.emit()
        try:
            found = await self._client.send({
                "@type": "searchChatMessages", "chat_id": self._chat_id, "topic_id": None,
                "query": "", "sender_id": None, "from_message_id": from_id, "offset": 0,
                "limit": PAGE, "filter": {"@type": TABS[tab]}})
        except TdError as e:
            log.info("Shared %s failed: %s", tab, e)
            found = {}
        finally:
            self._shared_busy = False
        if generation != self._generation or tab != self._tab:
            return
        messages = [m for m in found.get("messages") or [] if m]
        known = {row["messageId"] for row in self._shared}
        self._shared.extend(row for m in messages
                            if m["id"] not in known and (row := self._shared_row(m)))
        self._shared_next = int(found.get("next_from_message_id") or 0) if messages else 0
        self.sharedChanged.emit()

    # --- rows -------------------------------------------------------------------------------

    def _member_rows(self, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for member in members:
            sender = member.get("member_id") or {}
            user = self._users.users.get(sender.get("user_id", 0))
            if user is None:
                continue
            status = (member.get("status") or {}).get("@type", "")
            role = {"chatMemberStatusCreator": "owner",
                    "chatMemberStatusAdministrator": "admin"}.get(status, "")
            row = {
                "userId": user.id, "name": user.full_name, "role": role,
                "status": status_text(self._presence.statuses.get(user.id))
                if self._presence else "",
                "avatar": "", "initials": initials(user.full_name),
                "colorIndex": user.id % AVATAR_COLORS,
            }
            self._show_file(row, "avatar", user.photo_file_id, "avatar")
            rows.append(row)
        rows.sort(key=lambda r: (r["role"] == "", r["name"].lower()))
        return rows

    def _chat_row(self, chat_id: int) -> dict[str, Any]:
        chat = self._chats.chats[chat_id]
        row = {"chatId": chat.id, "title": chat.title, "initials": initials(chat.title),
               "avatar": "", "colorIndex": abs(chat.id) % AVATAR_COLORS}
        self._show_file(row, "avatar", chat.photo_file_id, "avatar")
        return row

    def _shared_row(self, message: dict[str, Any]) -> dict[str, Any] | None:
        content = message.get("content", {})
        date = message.get("date", 0)
        row = {"messageId": message["id"], "chatId": message.get("chat_id", self._chat_id),
               "kind": "", "image": "", "title": "", "subtitle": short_time(date) if date else "",
               "url": ""}
        if self._tab == "links":
            preview = message_link_preview(content)
            url = preview.url if preview else _first_url(content)
            if not url:
                return None
            row.update(kind="link", url=url, title=(preview.title if preview else "") or url,
                       subtitle=(preview.site if preview else "") or row["subtitle"])
            if preview and preview.image:
                self._chats.files.register(preview.image)
                self._show_file(row, "image", preview.image["id"], "media")
            return row
        media = extract(content)
        if media is None:
            return None
        row["kind"] = media.kind
        if media.preview:
            self._chats.files.register(media.preview)
            self._show_file(row, "image", media.preview["id"], "media")
        if media.kind in ("document", "audio"):
            size = (media.file or {}).get("size") or (media.file or {}).get("expected_size", 0)
            row["title"] = media.title or media.file_name or "File"
            row["subtitle"] = " · ".join(p for p in (human_size(size) if size else "",
                                                       row["subtitle"]) if p)
        elif media.kind == "voice":
            sender = message.get("sender_id") or {}
            user = self._users.users.get(sender.get("user_id", 0))
            row["title"] = user.full_name if user else content_preview(content)
            minutes, seconds = divmod(media.duration, 60)
            row["subtitle"] = f"{minutes}:{seconds:02d} · {row['subtitle']}"
        return row

    def _show_file(self, row: dict[str, Any], field: str, file_id: int | None,
                   kind: str) -> None:
        """row[field] = the picture's URL now, or once it's downloaded."""
        if file_id is None:
            return
        files = self._chats.files
        if files.path(file_id):
            row[field] = files.url(kind, file_id)
            return
        self._pending.setdefault(file_id, []).append((row, field, kind))
        files.download(file_id, AUTO_PRIORITY)

    def _photo_file(self) -> int | None:
        if self._user_id:
            user = self._users.users.get(self._user_id)
            return user.photo_file_id if user else None
        chat = self._chats.chats.get(self._chat_id)
        return chat.photo_file_id if chat else None

    def _on_chats(self, kind: str, payload: Any) -> None:
        if kind == "chat" and payload == self._chat_id and self._chat_id:
            self.changed.emit()  # mute, title, photo

    def _on_file(self, file_id: int) -> None:
        files = self._chats.files
        if not files.path(file_id):
            return
        if file_id == self._photo_file():
            self.changed.emit()
        waiting = self._pending.pop(file_id, [])
        for row, field, kind in waiting:
            row[field] = files.url(kind, file_id)
        if waiting:
            self.changed.emit()
            self.sharedChanged.emit()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _first_url(content: dict[str, Any]) -> str:
    text = content.get("text") or content.get("caption") or {}
    for entity in text.get("entities") or []:
        kind = entity.get("type") or {}
        if kind.get("@type") == "textEntityTypeTextUrl":
            return kind.get("url", "")
        if kind.get("@type") == "textEntityTypeUrl":
            raw = text.get("text", "")
            index = utf16_index_map(raw)  # offsets are UTF-16 code units
            start, end = entity["offset"], entity["offset"] + entity["length"]
            if end < len(index):
                return raw[index[start]:index[end]]
    return ""
