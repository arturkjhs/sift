"""Qt model of the open chat's messages (row 0 = newest; the view is BottomToTop)."""

from __future__ import annotations

import asyncio
import base64
import html
import logging
import re
from datetime import datetime
from enum import IntEnum, auto
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication

from ..services.ai import AiService
from ..services.summary import sender_key
from ..store.album import album_layout
from ..store.chats import ChatStore
from ..store.custom_emoji import CustomEmojiStore
from ..store.files import AUTO_PRIORITY, USER_PRIORITY, FileManager
from ..store.format import (
    clock,
    content_preview,
    day_label,
    initials,
    is_service,
    media_label,
    message_body,
)
from ..store.history import DELETED_REPLY, ChatHistory, Commit, Message
from ..store.media import (
    Media,
    album_id,
    decode_waveform,
    duration_text,
    extract,
    fit,
    human_size,
)
from ..store.presence import PresenceStore, members_text, status_text, typing_text
from ..store.reactions import (
    DEFAULT_REACTIONS,
    QUICK,
    as_items,
    available_keys,
    can_list_reactors,
    message_reactions,
    reaction_type,
    reactors_text,
)
from ..store.richtext import Palette, formatted_to_html
from ..store.users import UserStore
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

AVATAR_COLORS = 7
GROUP_WINDOW_SECONDS = 5 * 60
EDIT_WINDOW_SECONDS = 48 * 3600  # fallback when getMessageProperties isn't available
NEAR_JUMP_PAGES = 3  # a jump this close pages older history in; farther ones reload around it
ALBUM_WIDTH = 320  # albums are laid out for this width; QML scales down narrower bubbles
AUTOPLAY_MAX_BYTES = 10 * 1024 * 1024  # GIFs up to this size download and play by themselves
AnyIndex = QModelIndex | QPersistentModelIndex


class Role(IntEnum):
    MessageId = Qt.ItemDataRole.UserRole + 1
    IsOutgoing = auto()
    IsService = auto()
    ServiceText = auto()
    SenderName = auto()
    SenderKey = auto()
    SenderColor = auto()
    SenderInitials = auto()
    SenderAvatar = auto()
    ShowSender = auto()
    GroupBottom = auto()
    ShowAvatar = auto()
    Html = auto()
    Time = auto()
    Edited = auto()
    Status = auto()
    ReplyToId = auto()
    ReplySender = auto()
    ReplyText = auto()
    MediaLabel = auto()
    DayLabel = auto()
    MediaKind = auto()
    MediaSource = auto()
    MediaWidth = auto()
    MediaHeight = auto()
    FileId = auto()
    FileName = auto()
    FileInfo = auto()
    FileState = auto()
    FileProgress = auto()
    Duration = auto()
    Waveform = auto()
    StickerEmoji = auto()
    Transcript = auto()
    TranscriptState = auto()
    Reactions = auto()
    Translation = auto()
    TranslationState = auto()
    ForwardedFrom = auto()
    UnreadSeparator = auto()
    AlbumHidden = auto()  # a member of an album shown by its newest message: zero height
    AlbumItems = auto()  # on that newest message: [{messageId, kind, source, x, y, w, h, ...}]
    StickerFormat = auto()  # webp | tgs | webm
    PlaybackPath = auto()  # local file to play inline (animated sticker, GIF), "" until ready
    Selected = auto()  # in the multi-selection (Cmd/Ctrl/Shift-click)


# Roles that depend on file state (refreshed when a file of the message changes).
MEDIA_ROLES = [Role.MediaSource, Role.FileInfo, Role.FileState, Role.FileProgress,
               Role.SenderAvatar, Role.PlaybackPath, Role.AlbumItems]
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_VIEWER_KINDS = {"photo", "video", "animation", "videoNote"}


_STATUS_GLYPH = {"pending": "\u25f7", "sent": "\u2713", "read": "\u2713\u2713", "failed": "!"}


class _Adapter:
    """Binds history callbacks to the model only while that history is the current one, so a
    late page from a previously opened chat can't corrupt the model."""

    def __init__(self, model: MessageListModel, history_ref: list[ChatHistory]) -> None:
        self._model = model
        self._ref = history_ref

    def _current(self) -> bool:
        return bool(self._ref) and self._model._history is self._ref[0]

    def history_insert(self, row: int, count: int, commit: Commit) -> None:
        if not self._current():
            commit()
            return
        self._model.beginInsertRows(QModelIndex(), row, row + count - 1)
        commit()
        self._model.endInsertRows()
        self._model._refresh_albums(row - 1, row + count)

    def history_remove(self, row: int, count: int, commit: Commit) -> None:
        if not self._current():
            commit()
            return
        self._model.beginRemoveRows(QModelIndex(), row, row + count - 1)
        message_id = self._ref[0].messages[row]["id"]
        commit()
        self._model._html_cache.pop(message_id, None)
        self._model.endRemoveRows()
        self._model._refresh_albums(row - 1, row)
        if message_id in self._model._selected:
            self._model._selected.discard(message_id)
            self._model.selectionChanged.emit()

    def history_changed(self, row: int) -> None:
        if self._current():
            self._model._row_changed(row)

    def history_reset(self, commit: Commit) -> None:
        if not self._current():
            commit()
            return
        model = self._model
        model.beginResetModel()
        commit()
        model._html_cache = {}
        model._media_cache = {}
        model.endResetModel()
        model._sync_latest()


class MessageListModel(QAbstractListModel):
    chatChanged = Signal()
    loadingChanged = Signal()
    paletteChanged = Signal()
    jumpReady = Signal(int)  # row of a message requested by jumpTo(), once it's loaded
    viewerRequested = Signal("QVariant")  # message id of a photo/video to show full-window
    unreadReady = Signal("QVariant")  # first unread message id, once loaded after opening
    actionsReady = Signal("QVariant", "QVariantMap")  # message id, what can be done with it
    editReady = Signal("QVariant", str)  # message id, its text as markdown for the composer
    statusChanged = Signal()
    linkResolved = Signal("QVariant", "QVariant")  # chat id, message id (0: just the chat)
    latestChanged = Signal()
    countersChanged = Signal()
    selectionChanged = Signal()
    reactorsChanged = Signal()

    def __init__(
        self, client: TdClient, chats: ChatStore, users: UserStore,
        ai: AiService | None = None, presence: PresenceStore | None = None,
        parent: Any = None, emoji: CustomEmojiStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._chats = chats
        self._users = users
        self._history: ChatHistory | None = None
        self._loading = False
        self._viewed: set[int] = set()
        self._html_cache: dict[int, str] = {}
        self._media_cache: dict[int, Media | None] = {}
        self._file_messages: dict[int, set[int]] = {}  # file id -> message ids showing it
        self._unread_after = 0  # last read incoming message id when the chat was opened
        self._first_unread = 0  # message showing the "Unread messages" separator
        self._at_latest = True
        self._selected: set[int] = set()
        self._reactors: dict[tuple[int, str], list[str]] = {}  # (message, key) -> names
        self._reactors_asked: set[tuple[int, str]] = set()
        self._reactors_version = 0
        self._select_anchor = 0  # last message toggled: Shift-click selects from it
        self._files: FileManager = chats.files
        self._files.subscribe(self._on_file)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        self._palette = Palette(mono=mono)
        self._time_color = "#00000000"
        chats.subscribe(self._on_chat_store)
        self._ai = ai
        if ai is not None:
            ai.subscribe(self._on_ai)
        self._presence = presence
        if presence is not None:
            presence.subscribe(self._on_presence)
        self._emoji = emoji
        self._emoji_waiting: dict[str, set[int]] = {}  # custom emoji id -> message ids
        if emoji is not None:
            emoji.subscribe(self._on_emoji)

    # --- properties -------------------------------------------------------------------------

    @Property("QVariant", notify=chatChanged)
    def chatId(self) -> int:
        return self._history.chat_id if self._history else 0

    @Property(str, notify=chatChanged)
    def chatTitle(self) -> str:
        chat = self._chat()
        return chat.title if chat else ""

    @Property(str, notify=chatChanged)
    def chatType(self) -> str:
        chat = self._chat()
        return chat.type if chat else ""

    @Property(bool, notify=chatChanged)
    def canWrite(self) -> bool:
        chat = self._chat()
        return chat is not None and chat.type != "channel"  # TODO: real permissions

    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        return self._loading

    @Property(bool, notify=latestChanged)
    def atLatest(self) -> bool:
        """The newest message is loaded (false after opening on an old unread message or
        jumping far back: the view then loads newer pages and "down" reloads the newest)."""
        return self._at_latest

    @Property(int, notify=selectionChanged)
    def selectionCount(self) -> int:
        return len(self._selected)

    @Property(bool, notify=selectionChanged)
    def selectionCanDeleteForAll(self) -> bool:
        """Every selected message is own (or the chat is private): "delete for everyone"."""
        history = self._history
        chat = self._chat()
        if history is None or not self._selected:
            return False
        if chat is not None and chat.type == "private" and chat.peer_id != self._users.my_id:
            return True
        return all((history.get(i) or {}).get("is_outgoing") for i in self._selected)

    @Slot("QVariant")
    def toggleSelected(self, message_id: Any) -> None:
        message_id = int(message_id or 0)
        history = self._history
        message = history.get(message_id) if history else None
        if message is None or is_service(message.get("content", {})):
            return
        row = history.row_of(message_id)
        album = self._album_range(row)
        ids = [m["id"] for m in history.messages[album[0]:album[1] + 1]] if album else [message_id]
        if message_id in self._selected or any(i in self._selected for i in ids):
            self._selected.difference_update(ids)
        else:
            self._selected.update(ids)
        self._select_anchor = message_id
        self._selection_rows(ids)

    @Slot("QVariant")
    def selectRange(self, message_id: Any) -> None:
        """Shift-click: everything between the last toggled message and this one."""
        history = self._history
        message_id = int(message_id or 0)
        if history is None or history.row_of(message_id) < 0:
            return
        anchor = history.row_of(self._select_anchor) if self._select_anchor else -1
        if anchor < 0:
            self.toggleSelected(message_id)
            return
        lo, hi = sorted((anchor, history.row_of(message_id)))
        ids = [m["id"] for m in history.messages[lo:hi + 1]
               if not is_service(m.get("content", {}))]
        self._selected.update(ids)
        self._selection_rows(ids)

    @Slot()
    def clearSelection(self) -> None:
        ids = list(self._selected)
        self._selected.clear()
        self._select_anchor = 0
        self._selection_rows(ids)

    @Slot(bool)
    def deleteSelected(self, revoke: bool) -> None:
        history = self._history
        if history is None or not self._selected:
            return
        self._spawn(self._request({
            "@type": "deleteMessages", "chat_id": history.chat_id,
            "message_ids": sorted(self._selected), "revoke": bool(revoke),
        }, "Deleting"))
        self.clearSelection()

    @Slot("QVariant")
    def forwardSelected(self, to_chat_id: Any) -> None:
        history = self._history
        if history is None or not self._selected or not to_chat_id:
            return
        self._spawn(self._request({
            "@type": "forwardMessages", "chat_id": int(to_chat_id), "topic_id": None,
            "from_chat_id": history.chat_id, "message_ids": sorted(self._selected),
            "options": None, "send_copy": False, "remove_caption": False,
        }, "Forwarding"))
        self.clearSelection()

    @Slot()
    def copySelected(self) -> None:
        """Texts of the selected messages, oldest first, with sender and time like other
        clients ("Olena, [12:30]\ntext")."""
        history = self._history
        if history is None or not self._selected:
            return
        parts = []
        for message_id in sorted(self._selected):
            message = history.get(message_id)
            if message is None:
                continue
            text = (message_body(message.get("content", {})) or {}).get("text", "") or (
                content_preview(message.get("content", {})))
            parts.append(f"{self._sender_name(message)}, [{clock(message.get('date', 0))}]\n"
                         f"{text}")
        QGuiApplication.clipboard().setText("\n\n".join(parts))
        self.clearSelection()

    def _selection_rows(self, ids: list[int]) -> None:
        history = self._history
        for message_id in ids:
            row = history.row_of(message_id) if history else -1
            if row >= 0:
                album = self._album_range(row)
                for each in range(album[0], album[1] + 1) if album else (row,):
                    index = self.index(each)
                    self.dataChanged.emit(index, index, [Role.Selected])
        self.selectionChanged.emit()

    @Property(int, notify=reactorsChanged)
    def reactorsVersion(self) -> int:
        """Bumped when names for reactorsText() arrive (QML re-evaluates its tooltips)."""
        return self._reactors_version

    @Slot("QVariant", str, result=str)
    def reactorsText(self, message_id: Any, key: str) -> str:
        """Who set a reaction, for its tooltip: the recent senders TDLib sends along, then the
        full list (getMessageAddedReactions, where allowed) once fetched."""
        history = self._history
        message = history.get(int(message_id or 0)) if history else None
        reaction = next((r for r in message_reactions(message) if r.key == key),
                        None) if message else None
        if history is None or message is None or reaction is None:
            return ""
        cache_key = (message["id"], key)
        names = self._reactors.get(cache_key)
        if names is None:
            names = [self._sender_obj_name(s) for s in reaction.recent]
            if (len(names) < reaction.count and can_list_reactors(message)
                    and cache_key not in self._reactors_asked):
                self._reactors_asked.add(cache_key)
                self._spawn(self._fetch_reactors(history, message["id"], key))
        return reactors_text(names, reaction.count)

    async def _fetch_reactors(self, history: ChatHistory, message_id: int, key: str) -> None:
        try:
            found = await self._client.send({
                "@type": "getMessageAddedReactions", "chat_id": history.chat_id,
                "message_id": message_id, "reaction_type": reaction_type(key), "offset": "",
                "limit": 50})
        except TdError as e:
            log.info("getMessageAddedReactions failed: %s", e)
            return
        if history is not self._history:
            return
        self._reactors[(message_id, key)] = [
            self._sender_obj_name(r.get("sender_id") or {}) for r in found.get("reactions") or []]
        self._reactors_version += 1
        self.reactorsChanged.emit()

    def _sender_obj_name(self, sender: dict[str, Any]) -> str:
        return self._sender_name({"sender_id": sender})

    @Property(int, notify=countersChanged)
    def unreadCount(self) -> int:
        chat = self._chat()
        return chat.unread_count if chat else 0

    @Property(int, notify=countersChanged)
    def mentionCount(self) -> int:
        chat = self._chat()
        return chat.unread_mention_count if chat else 0

    @Property(int, notify=countersChanged)
    def reactionCount(self) -> int:
        chat = self._chat()
        return chat.unread_reaction_count if chat else 0

    @Property(str, notify=statusChanged)
    def chatStatus(self) -> str:
        """Under the title: typing, online / last seen, or member counts."""
        typing = self._typing_text()
        return typing or self._presence_text()

    @Property(bool, notify=statusChanged)
    def chatStatusActive(self) -> bool:
        """Typing or online: drawn in the accent color."""
        chat = self._chat()
        if self._typing_text():
            return True
        return bool(chat and self._presence and chat.type in ("private", "secret")
                    and self._presence.is_online(chat.peer_id))

    @Slot()
    def refreshStatus(self) -> None:
        """"last seen 5 minutes ago" ages: QML calls this periodically."""
        self.statusChanged.emit()

    def _get_link(self) -> str:
        return self._palette.link

    def _set_link(self, value: str) -> None:
        self._set_palette(link=value)

    def _get_code(self) -> str:
        return self._palette.code_background

    def _set_code(self, value: str) -> None:
        self._set_palette(code_background=value)

    def _get_spoiler(self) -> str:
        return self._palette.spoiler

    def _set_spoiler(self, value: str) -> None:
        self._set_palette(spoiler=value)

    linkColor = Property(str, _get_link, _set_link, notify=paletteChanged)
    codeBackground = Property(str, _get_code, _set_code, notify=paletteChanged)
    spoilerColor = Property(str, _get_spoiler, _set_spoiler, notify=paletteChanged)

    # --- QML API ----------------------------------------------------------------------------

    @Slot("QVariant")
    def open(self, chat_id: Any) -> None:
        chat_id = int(chat_id or 0)
        if self._history and self._history.chat_id == chat_id:
            return
        self.close()
        if not chat_id:
            return
        ref: list[ChatHistory] = []
        history = ChatHistory(self._client, chat_id, _Adapter(self, ref),
                              last_message_id=lambda: self._last_message_id(chat_id))
        ref.append(history)
        self.beginResetModel()
        self._history = history
        self._viewed = set()
        self._html_cache = {}
        self._media_cache = {}
        self._file_messages = {}
        chat = self._chats.chats.get(chat_id)
        self._unread_after = (chat.last_read_inbox_message_id
                              if chat and chat.unread_count > 0 else 0)
        self._first_unread = 0
        self._selected = set()
        self._select_anchor = 0
        self.endResetModel()
        self.chatChanged.emit()
        self.statusChanged.emit()
        self.countersChanged.emit()
        self.selectionChanged.emit()
        self._sync_latest()
        self._spawn(self._client.send({"@type": "openChat", "chat_id": chat_id}))
        self._spawn(self._run_load(history, self._load_initial(history)))

    @Slot()
    def close(self) -> None:
        if self._history is None:
            return
        history = self._history
        history.dispose()
        self.beginResetModel()
        self._history = None
        self._html_cache = {}
        self._media_cache = {}
        self._file_messages = {}
        self.endResetModel()
        self.chatChanged.emit()
        self._spawn(self._client.send({"@type": "closeChat", "chat_id": history.chat_id}))

    @Slot()
    def loadOlder(self) -> None:
        history = self._history
        if history and not history.loading and not history.reached_start:
            self._spawn(self._run_load(history, history.load_older()))

    @Slot()
    def loadNewer(self) -> None:
        history = self._history
        if history and not history.loading and not history.reached_end:
            self._spawn(self._run_load(history, self._load_newer(history)))

    async def _load_newer(self, history: ChatHistory) -> None:
        await history.load_newer()
        if history is self._history:
            self._sync_latest()

    @Slot()
    def jumpToLatest(self) -> None:
        """"Down" when the window stops short of the newest message: reload the newest page."""
        history = self._history
        if history is not None and not history.reached_end:
            self._spawn(self._run_load(history, self._load_latest(history)))

    async def _load_latest(self, history: ChatHistory) -> None:
        await history.load_latest()
        if history is self._history:
            self._sync_latest()

    @Slot()
    def nextMention(self) -> None:
        """Jump to the oldest unread mention (viewing it marks it read)."""
        self._spawn(self._jump_to_unread("searchMessagesFilterUnreadMention"))

    @Slot()
    def nextReaction(self) -> None:
        """Jump to the oldest own message with an unread reaction."""
        self._spawn(self._jump_to_unread("searchMessagesFilterUnreadReaction"))

    @Slot()
    def readAllMentions(self) -> None:
        if self._history is not None:
            self._spawn(self._request({"@type": "readAllChatMentions",
                                       "chat_id": self._history.chat_id}, "Reading mentions"))

    @Slot()
    def readAllReactions(self) -> None:
        if self._history is not None:
            self._spawn(self._request({"@type": "readAllChatReactions",
                                       "chat_id": self._history.chat_id}, "Reading reactions"))

    async def _jump_to_unread(self, kind: str) -> None:
        history = self._history
        if history is None:
            return
        try:
            found = await self._client.send({
                "@type": "searchChatMessages", "chat_id": history.chat_id, "topic_id": None,
                "query": "", "sender_id": None, "from_message_id": 0, "offset": 0,
                "limit": 100, "filter": {"@type": kind}})
        except TdError as e:
            log.info("Searching %s failed: %s", kind, e)
            return
        ids = [m["id"] for m in found.get("messages") or [] if m]
        if ids and history is self._history:
            target = min(ids)  # the oldest one: read them in order
            if kind.endswith("Reaction"):  # reactions are read when the message is viewed
                self._viewed.discard(target)
            self.jumpTo(target)

    @Slot(str, "QVariant")
    def send(self, text: str, reply_to: Any = 0) -> None:
        history = self._history
        if history is None or not text.strip():
            return
        self._clear_unread_separator()
        self._spawn(self._send(history.chat_id, text, int(reply_to or 0)))

    @Slot(int, int)
    def markViewed(self, first: int, last: int) -> None:
        history = self._history
        if history is None or first < 0 or last < 0:
            return
        lo, hi = sorted((first, last))
        ids = [
            m["id"] for m in history.messages[lo : hi + 1]
            if m["id"] not in self._viewed and not m.get("is_outgoing")
        ]
        if not ids:
            return
        self._viewed.update(ids)
        self._spawn(self._client.send({
            "@type": "viewMessages", "chat_id": history.chat_id, "message_ids": ids,
            "source": None, "force_read": True,
        }))

    @Slot("QVariant")
    def requestActions(self, message_id: Any) -> None:
        """What the context menu may offer (edit, delete, reactions); answers via actionsReady."""
        history = self._history
        message = history.get(int(message_id or 0)) if history else None
        if history is not None and message is not None:
            self._spawn(self._actions(history.chat_id, message))

    @Slot("QVariant", str)
    def addReaction(self, message_id: Any, key: str) -> None:
        """Set a reaction (the AI panel's suggestions); no-op if it is already set."""
        history = self._history
        message = history.get(int(message_id or 0)) if history else None
        if message is not None and not any(
                r.key == key and r.chosen for r in message_reactions(message)):
            self.toggleReaction(message_id, key)

    @Slot("QVariant", str)
    def toggleReaction(self, message_id: Any, key: str) -> None:
        history = self._history
        message = history.get(int(message_id or 0)) if history else None
        if history is None or message is None or not key:
            return
        chosen = any(r.key == key and r.chosen for r in message_reactions(message))
        request: dict[str, Any] = {
            "@type": "removeMessageReaction" if chosen else "addMessageReaction",
            "chat_id": history.chat_id, "message_id": message["id"],
            "reaction_type": reaction_type(key),
        }
        if not chosen:
            request.update(is_big=False, update_recent_reactions=True)
        self._spawn(self._request(request, "Reaction"))

    @Slot("QVariant")
    def startEdit(self, message_id: Any) -> None:
        """Fetch the message text as markdown (entities kept) and emit editReady."""
        history = self._history
        message = history.get(int(message_id or 0)) if history else None
        if message is not None:
            self._spawn(self._start_edit(message))

    @Slot("QVariant", str)
    def saveEdit(self, message_id: Any, text: str) -> None:
        history = self._history
        message = history.get(int(message_id or 0)) if history else None
        if history is not None and message is not None:
            self._spawn(self._save_edit(history.chat_id, message, text))

    @Slot(result="QVariant")
    def lastEditableId(self) -> int:
        """Newest own message that can be edited (Up arrow in an empty composer)."""
        history = self._history
        for message in history.messages if history else []:
            if self._can_edit_guess(message):
                return message["id"]
        return 0

    @Slot("QVariant", bool)
    def deleteMessage(self, message_id: Any, revoke: bool) -> None:
        history = self._history
        if history is None or not message_id:
            return
        self._spawn(self._request({
            "@type": "deleteMessages", "chat_id": history.chat_id,
            "message_ids": [int(message_id)], "revoke": bool(revoke),
        }, "Deleting"))

    @Slot("QVariant", "QVariant")
    def forward(self, message_id: Any, to_chat_id: Any) -> None:
        history = self._history
        if history is None or not message_id or not to_chat_id:
            return
        self._spawn(self._request({
            "@type": "forwardMessages", "chat_id": int(to_chat_id), "topic_id": None,
            "from_chat_id": history.chat_id, "message_ids": [int(message_id)],
            "options": None, "send_copy": False, "remove_caption": False,
        }, "Forwarding"))

    @Slot("QVariant", result=int)
    def rowOf(self, message_id: Any) -> int:
        return self._history.row_of(int(message_id)) if self._history else -1

    @Slot(str)
    def openLink(self, link: str) -> None:
        """Telegram links to a message (t.me/<chat>/<id>, t.me/c/…, tg://…) open inside the
        client (linkResolved), anything else in the browser."""
        if not _TELEGRAM_LINK.match(link):
            QDesktopServices.openUrl(QUrl(link))
            return
        self._spawn(self._open_link(link))

    async def _open_link(self, link: str) -> None:
        try:
            info = await self._client.send({"@type": "getMessageLinkInfo", "url": link})
        except TdError as e:
            log.info("Not a message link (%s): %s", e.message, link)
            info = {}
        chat_id = info.get("chat_id") or 0
        if chat_id and chat_id in self._chats.chats:
            self.linkResolved.emit(chat_id, (info.get("message") or {}).get("id") or 0)
        elif not link.startswith("tg:"):
            QDesktopServices.openUrl(QUrl(link))

    @Slot("QVariant")
    def jumpTo(self, message_id: Any) -> None:
        """Scroll to a message, paging older history in first if needed (emits jumpReady)."""
        history = self._history
        message_id = int(message_id or 0)
        if history is None or not message_id:
            return
        row = history.row_of(message_id)
        if row >= 0:
            self.jumpReady.emit(row)
            return

        async def load() -> None:
            near = history.messages and message_id < history.messages[-1]["id"]
            found = near and await history.load_until(message_id, max_pages=NEAR_JUMP_PAGES)
            if not found and history is self._history:
                found = await history.load_around(message_id)
            if found and history is self._history:
                self._sync_latest()
                self.jumpReady.emit(history.row_of(message_id))

        self._spawn(self._run_load(history, load()))

    @Slot("QVariant", result="QVariantMap")
    def replyPreview(self, message_id: Any) -> dict[str, str]:
        message = self._history.get(int(message_id or 0)) if self._history else None
        if message is None:
            return {"sender": "", "text": ""}
        return {"sender": self._sender_name(message), "text": self._short_text(message)}

    @Slot("QVariant")
    def copyText(self, message_id: Any) -> None:
        message = self._history.get(int(message_id or 0)) if self._history else None
        if message is None:
            return
        body = message_body(message.get("content", {}))
        text = (body or {}).get("text", "")
        if not text and self._ai is not None and self._history is not None:
            transcript = self._ai.transcript(self._history.chat_id, message["id"])
            text = transcript.text if transcript and transcript.state == "done" else ""
        QGuiApplication.clipboard().setText(text)

    @Slot("QVariant")
    def activateMedia(self, message_id: Any) -> None:
        """Click on media: open if downloaded, cancel if downloading, otherwise download
        (photos and videos then open automatically)."""
        message = self._history.get(int(message_id or 0)) if self._history else None
        media = self._media(message) if message else None
        if media is None or not media.file:
            return
        if media.kind in _VIEWER_KINDS:
            self.viewerRequested.emit(message["id"])  # the viewer downloads what it shows
            return
        file_id = media.file["id"]
        state = self._files.get(file_id)
        status = state.status if state else "remote"
        if status == "ready":
            self.openFile(file_id)
        elif status == "downloading":
            self._files.cancel(file_id)
        elif status == "remote":
            self._files.download(file_id, USER_PRIORITY)

    @Slot("QVariant")
    def openFile(self, file_id: Any) -> None:
        path = self._files.path(int(file_id or 0))
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    @Slot("QVariant")
    def showInFolder(self, file_id: Any) -> None:
        path = self._files.path(int(file_id or 0))
        if path:  # TODO: select the file (Finder: `open -R`, Linux: org.freedesktop.FileManager1)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(_parent(path))))

    @Slot("QVariantMap", "QVariant")
    def sendSticker(self, sticker: dict[str, Any], reply_to: Any = 0) -> None:
        history = self._history
        file = (sticker or {}).get("sticker") or {}
        if history is None or not file.get("id"):
            return
        content = {
            "@type": "inputMessageSticker",
            # Recent TDLib wraps the file in inputSticker (with its size).
            "sticker": {
                "@type": "inputSticker",
                "sticker": {"@type": "inputFileId", "id": file["id"]},
                "thumbnail": None,
                "width": sticker.get("width", 512),
                "height": sticker.get("height", 512),
            },
            "emoji": sticker.get("emoji", ""),
        }
        self._spawn(self._send_content(history.chat_id, content, int(reply_to or 0)))

    def send_voice_to(self, chat_id: int, path: str, duration: int, waveform: bytes,
                      reply_to: int = 0) -> None:
        """A recorded voice message (Ogg/Opus). The chat is the one it was recorded in, which
        may no longer be open."""
        if self._history is not None and self._history.chat_id == chat_id:
            self._clear_unread_separator()
        content = {
            "@type": "inputMessageVoiceNote",
            "voice_note": {"@type": "inputVoiceNote",
                           "voice_note": {"@type": "inputFileLocal", "path": path},
                           "duration": duration,
                           "waveform": base64.b64encode(waveform).decode("ascii")},
            "caption": None, "self_destruct_type": None,
        }
        self._spawn(self._send_content(chat_id, content, reply_to))

    def send_file(self, path: str, caption: str = "", reply_to: int = 0) -> None:
        self.send_files([(path, caption, reply_to)])

    def send_files(self, files: list[tuple[str, str, int]]) -> None:
        """Send local files (path, caption, reply-to) to the open chat one after another, so
        they arrive in order (ComposerModel's staged attachments)."""
        history = self._history
        if history is not None and files:
            self._clear_unread_separator()
            self._spawn(self._send_files(history.chat_id, files))

    async def _send_files(self, chat_id: int, files: list[tuple[str, str, int]]) -> None:
        for path, caption, reply_to in files:
            await self._send_file(chat_id, path, caption, reply_to)

    # --- QAbstractListModel -----------------------------------------------------------------

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid() or self._history is None:
            return 0
        return len(self._history.messages)

    def roleNames(self) -> dict[int, QByteArray]:
        return {role.value: QByteArray((role.name[0].lower() + role.name[1:]).encode())
                for role in Role}

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        history = self._history
        if history is None or not index.isValid():
            return None
        row = index.row()
        if not 0 <= row < len(history.messages):
            return None
        message = history.messages[row]
        content = message.get("content", {})

        if row >= len(history.messages) - 10:
            self.loadOlder()  # near the visual top: prefetch the next page
        elif row < 10 and not history.reached_end:
            self.loadNewer()  # near the bottom of a window that stops short of the newest

        match role:
            case Role.MessageId:
                return message["id"]
            case Role.IsOutgoing:
                return bool(message.get("is_outgoing"))
            case Role.IsService:
                return is_service(content)
            case Role.ServiceText:
                if not is_service(content):
                    return ""
                return f"{self._sender_name(message)} {content_preview(content)}".strip()
            case Role.SenderName:
                return self._sender_name(message)
            case Role.SenderKey:
                return sender_key(message)
            case Role.SenderColor:
                return abs(self._sender_key(message)) % AVATAR_COLORS
            case Role.SenderInitials:
                return initials(self._sender_name(message))
            case Role.SenderAvatar:
                return self._sender_avatar(message)
            case Role.ShowSender:
                return self._is_group_chat() and not message.get("is_outgoing") and not (
                    self._same_group(message, self._neighbor(self._older_row(row))))
            case Role.GroupBottom:
                return not self._same_group(message, self._neighbor(row - 1))
            case Role.ShowAvatar:
                return (self._is_group_chat() and not message.get("is_outgoing")
                        and not self._same_group(message, self._neighbor(row - 1)))
            case Role.Html:
                album = self._album_range(row)
                if album and row == album[0]:
                    return self._album_html(album)
                return self._html(message)
            case Role.Time:
                return clock(message.get("date", 0))
            case Role.Edited:
                return bool(message.get("edit_date"))
            case Role.Status:
                return self._status(message)
            case Role.ReplyToId:
                return history.reply_target_id(message)
            case Role.ReplySender:
                reply = history.reply_message(message)
                if reply is None:
                    return ""
                if reply is DELETED_REPLY:
                    return "Deleted message"
                return self._sender_name(reply)
            case Role.ReplyText:
                reply = history.reply_message(message)
                if reply is None or reply is DELETED_REPLY:
                    return ""
                return self._short_text(reply)
            case Role.MediaLabel:
                return "" if self._media(message) else media_label(content)
            case Role.Transcript | Role.TranscriptState:
                transcript = (self._ai.transcript(history.chat_id, message["id"])
                              if self._ai else None)
                if role == Role.Transcript:
                    return transcript.text if transcript else ""
                return transcript.state if transcript else ""
            case Role.Translation | Role.TranslationState:
                translation = (self._ai.translation(history.chat_id, message["id"])
                               if self._ai else None)
                if role == Role.Translation:
                    return translation.text if translation else ""
                return translation.state if translation else ""
            case Role.Reactions:
                return [{"key": r.key, "label": r.label, "count": r.count, "chosen": r.chosen,
                         "image": self._emoji_url(r.key[7:], message["id"])
                         if r.key.startswith("custom:") else ""}
                        for r in message_reactions(message)]
            case Role.ForwardedFrom:
                return self._forwarded_from(message)
            case Role.UnreadSeparator:
                if not self._first_unread:
                    return False
                album = self._album_range(row)
                if album:
                    return row == album[0] and any(
                        m["id"] == self._first_unread
                        for m in history.messages[album[0]:album[1] + 1])
                return message["id"] == self._first_unread
            case Role.AlbumHidden:
                album = self._album_range(row)
                return bool(album) and row != album[0]
            case Role.Selected:
                album = self._album_range(row)
                members = history.messages[album[0]:album[1] + 1] if album else [message]
                return any(m["id"] in self._selected for m in members)
            case Role.DayLabel:
                older = self._neighbor(self._older_row(row))
                if older is None:
                    return day_label(message.get("date", 0)) if history.reached_start else ""
                if _day(older) != _day(message):
                    return day_label(message.get("date", 0))
                return ""
        return self._media_data(message, role)

    def _media_data(self, message: Message, role: int) -> Any:
        if role in (Role.MediaKind, Role.MediaWidth, Role.MediaHeight, Role.AlbumItems):
            history = self._history
            row = history.row_of(message["id"]) if history else -1
            album = self._album_range(row) if row >= 0 else None
            if album and row == album[0]:
                items = self._album_items(album)
                match role:
                    case Role.MediaKind:
                        return "album"
                    case Role.MediaWidth:
                        return ALBUM_WIDTH
                    case Role.MediaHeight:
                        return max((i["y"] + i["h"] for i in items), default=0)
                return items
            if role == Role.AlbumItems:
                return []
        media = self._media(message)
        if media is None:
            return {Role.MediaKind: "", Role.MediaSource: "", Role.MediaWidth: 0,
                    Role.MediaHeight: 0, Role.FileId: 0, Role.FileName: "", Role.FileInfo: "",
                    Role.FileState: "", Role.FileProgress: 0.0, Role.Duration: "",
                    Role.Waveform: [], Role.StickerEmoji: "", Role.StickerFormat: "",
                    Role.PlaybackPath: ""}.get(role)
        main = self._files.get(media.file["id"]) if media.file else None
        match role:
            case Role.MediaKind:
                return media.kind
            case Role.MediaSource:
                return self._media_source(media)
            case Role.MediaWidth | Role.MediaHeight:
                width, height = _display_size(media)
                return width if role == Role.MediaWidth else height
            case Role.FileId:
                return media.file["id"] if media.file else 0
            case Role.FileName:
                return media.title or media.file_name or _default_name(media.kind)
            case Role.FileInfo:
                if main is None:
                    return ""
                if main.status in ("downloading", "uploading"):
                    done = main.uploaded if main.status == "uploading" else main.downloaded
                    return f"{human_size(done)} / {human_size(main.size)}"
                info = human_size(main.size) if main.size else ""
                if media.kind == "audio" and media.duration:
                    info = f"{duration_text(media.duration)}, {info}" if info else duration_text(
                        media.duration)
                return info
            case Role.FileState:
                return main.status if main else "remote"
            case Role.FileProgress:
                return main.progress if main else 0.0
            case Role.Duration:
                return duration_text(media.duration) if media.duration else ""
            case Role.Waveform:
                return decode_waveform(media.waveform) if media.kind == "voice" else []
            case Role.StickerEmoji:
                return media.emoji
            case Role.StickerFormat:
                return media.format
            case Role.PlaybackPath:
                return self._playback_path(media, main)
        return None

    def _playback_path(self, media: Media, main: Any) -> str:
        """Animated stickers and small GIFs play inline: download them as they scroll by."""
        animated = media.kind == "sticker" and media.format in ("tgs", "webm")
        gif = media.kind == "animation" and 0 < (main.size if main else 0) <= AUTOPLAY_MAX_BYTES
        if not media.file or not (animated or gif):
            return ""
        path = self._files.path(media.file["id"])
        if path:
            return path
        self._files.download(media.file["id"], AUTO_PRIORITY)
        return ""

    # --- albums -----------------------------------------------------------------------------

    def _album_range(self, row: int) -> tuple[int, int] | None:
        """(newest row, oldest row) of the loaded album containing `row`, if 2+ are loaded."""
        messages = self._history.messages if self._history else []
        if not 0 <= row < len(messages):
            return None
        group = album_id(messages[row])
        if not group:
            return None
        low = high = row
        while low > 0 and album_id(messages[low - 1]) == group:
            low -= 1
        while high + 1 < len(messages) and album_id(messages[high + 1]) == group:
            high += 1
        return (low, high) if high > low else None

    def _older_row(self, row: int) -> int:
        """The next older row that is shown (skips the hidden members of an album)."""
        album = self._album_range(row)
        return album[1] + 1 if album and row == album[0] else row + 1

    def _album_items(self, album: tuple[int, int]) -> list[dict[str, Any]]:
        assert self._history is not None
        members = list(reversed(self._history.messages[album[0]:album[1] + 1]))  # oldest first
        medias = [self._media(m) for m in members]
        sizes = [(m.width, m.height) if m else (1, 1) for m in medias]
        cells = album_layout(sizes, ALBUM_WIDTH)
        items = []
        for message, media, cell in zip(members, medias, cells, strict=True):
            main = self._files.get(media.file["id"]) if media and media.file else None
            items.append({
                "messageId": message["id"], "kind": media.kind if media else "",
                "source": self._media_source(media) if media else "",
                "x": cell.x, "y": cell.y, "w": cell.width, "h": cell.height,
                "fileState": main.status if main else "remote",
                "progress": main.progress if main else 0.0,
                "duration": duration_text(media.duration) if media and media.duration else "",
            })
        return items

    def _album_html(self, album: tuple[int, int]) -> str:
        """The album's caption (whichever member has it) with the newest member's time."""
        assert self._history is not None
        newest = self._history.messages[album[0]]
        captioned = next((m for m in reversed(self._history.messages[album[0]:album[1] + 1])
                          if message_body(m.get("content", {}))), None)
        if captioned is None:
            return ""
        return formatted_to_html(message_body(captioned.get("content", {})), self._palette,
                                 self._time_spacer(newest), self._emoji_callback(newest["id"]))

    def _refresh_albums(self, first: int, last: int) -> None:
        """An album gained or lost a member: all its rows change (hidden/shown, grid)."""
        history = self._history
        if history is None:
            return
        done: set[tuple[int, int]] = set()
        for row in range(max(0, first), min(len(history.messages), last + 1)):
            album = self._album_range(row)
            if album is None or album in done:
                continue
            done.add(album)
            for member in history.messages[album[0]:album[1] + 1]:
                self._html_cache.pop(member["id"], None)
            self.dataChanged.emit(self.index(album[0]), self.index(album[1]))
            if album[1] + 1 < len(history.messages):  # the row above may regain its name
                self.dataChanged.emit(self.index(album[1] + 1), self.index(album[1] + 1))

    def _media(self, message: Message) -> Media | None:
        message_id = message["id"]
        if message_id in self._media_cache:
            return self._media_cache[message_id]
        media = extract(message.get("content", {}))
        self._media_cache[message_id] = media
        if media is not None:
            for file in (media.file, media.preview):
                if file:
                    self._files.register(file)
                    self._file_messages.setdefault(file["id"], set()).add(message_id)
            if media.preview and media.minithumbnail:
                self._files.minithumbnails[media.preview["id"]] = media.minithumbnail
        return media

    def _media_source(self, media: Media) -> str:
        """Image URL for inline display; starts small automatic downloads of previews."""
        preview = media.preview
        if preview is None:
            return ""
        preview_id = preview["id"]
        if self._files.path(preview_id):
            if media.kind == "sticker":
                return self._files.url("sticker", preview_id)
            if media.kind == "videoNote":
                return self._files.url("avatar", preview_id)
            return self._files.url("media", preview_id)
        self._files.download(preview_id, AUTO_PRIORITY)
        if preview_id in self._files.minithumbnails and media.kind != "sticker":
            return self._files.url("mini", preview_id)
        return ""

    # --- internals --------------------------------------------------------------------------

    def _chat(self):
        return self._chats.chats.get(self._history.chat_id) if self._history else None

    def _is_group_chat(self) -> bool:
        chat = self._chat()
        return chat is not None and chat.type in ("group", "supergroup")

    def _neighbor(self, row: int) -> Message | None:
        messages = self._history.messages if self._history else []
        return messages[row] if 0 <= row < len(messages) else None

    def _same_group(self, a: Message, b: Message | None) -> bool:
        if b is None or is_service(a.get("content", {})) or is_service(b.get("content", {})):
            return False
        return (
            a.get("sender_id") == b.get("sender_id")
            and abs(a.get("date", 0) - b.get("date", 0)) < GROUP_WINDOW_SECONDS
            and _day(a) == _day(b)
        )

    def _sender_key(self, message: Message) -> int:
        sender = message.get("sender_id", {})
        return int(sender.get("user_id") or sender.get("chat_id") or 0)

    def _sender_name(self, message: Message) -> str:
        sender = message.get("sender_id", {})
        if sender.get("@type") == "messageSenderUser":
            user = self._users.users.get(sender.get("user_id", 0))
            return user.full_name if user else ""
        if sender.get("@type") == "messageSenderChat":
            chat = self._chats.chats.get(sender.get("chat_id", 0))
            return chat.title if chat else ""
        return ""

    def _sender_avatar(self, message: Message) -> str:
        """Profile photo of the sender (user or chat); starts a small download when needed."""
        sender = message.get("sender_id", {})
        if sender.get("@type") == "messageSenderUser":
            user = self._users.users.get(sender.get("user_id", 0))
            file_id = user.photo_file_id if user else None
        else:
            chat = self._chats.chats.get(sender.get("chat_id", 0))
            file_id = chat.photo_file_id if chat else None
        if file_id is None:
            return ""
        self._file_messages.setdefault(file_id, set()).add(message["id"])
        if self._files.path(file_id):
            return self._files.url("avatar", file_id)
        self._files.download(file_id, AUTO_PRIORITY)
        return ""

    def _forwarded_from(self, message: Message) -> str:
        origin = (message.get("forward_info") or {}).get("origin") or {}
        match origin.get("@type"):
            case "messageOriginUser" | "messageForwardOriginUser":
                user = self._users.users.get(origin.get("sender_user_id", 0))
                return user.full_name if user else "Unknown"
            case "messageOriginHiddenUser" | "messageForwardOriginHiddenUser":
                return origin.get("sender_name", "")
            case "messageOriginChat" | "messageOriginChannel" | "messageForwardOriginChat" | \
                    "messageForwardOriginChannel":
                chat = self._chats.chats.get(origin.get("sender_chat_id") or origin.get("chat_id")
                                             or 0)
                title = chat.title if chat else "Unknown"
                signature = origin.get("author_signature", "")
                return f"{title} ({signature})" if signature else title
        return ""

    def _typing_text(self) -> str:
        chat = self._chat()
        if chat is None or self._presence is None:
            return ""
        return typing_text(self._presence.typing.get(chat.id, []), self._sender_key_name,
                           chat.type in ("private", "secret"))

    def _sender_key_name(self, key: int) -> str:
        user = self._users.users.get(key)
        if user is not None:
            return user.full_name
        chat = self._chats.chats.get(key)
        return chat.title if chat else ""

    def _presence_text(self) -> str:
        chat = self._chat()
        presence = self._presence
        if chat is None or presence is None:
            return ""
        if chat.type in ("private", "secret"):
            if chat.peer_id == self._users.my_id:
                return ""  # Saved Messages
            if chat.peer_id in presence.bots:
                return "bot"
            return status_text(presence.statuses.get(chat.peer_id))
        return members_text(presence.members.get(chat.peer_id, 0),
                            presence.online_count.get(chat.id, 0), chat.type == "channel")

    def _on_presence(self, kind: str, payload: Any) -> None:
        chat = self._chat()
        if chat is None:
            return
        if (kind in ("typing", "online_count") and payload == chat.id) or (
                kind in ("user", "members") and payload == chat.peer_id):
            self.statusChanged.emit()

    def _can_edit_guess(self, message: Message) -> bool:
        content = message.get("content", {})
        if "can_be_edited" in message:  # older TDLib
            return bool(message["can_be_edited"]) and _has_text_field(content)
        state = (message.get("sending_state") or {}).get("@type")
        chat = self._chat()
        saved = chat is not None and chat.peer_id == self._users.my_id and chat.type == "private"
        return (bool(message.get("is_outgoing")) and state is None and not is_service(content)
                and _has_text_field(content)
                and (saved or _now() - message.get("date", 0) < EDIT_WINDOW_SECONDS))

    def _clear_unread_separator(self) -> None:
        """Sending something: the separator goes and the view returns to the newest message."""
        if self._first_unread and self._history:
            row = self._history.row_of(self._first_unread)
            self._first_unread = 0
            self._row_changed(row)
        self.jumpToLatest()

    def _last_message_id(self, chat_id: int) -> int:
        chat = self._chats.chats.get(chat_id)
        return int(((chat.last_message if chat else None) or {}).get("id") or 0)

    def _sync_latest(self) -> None:
        latest = self._history.reached_end if self._history else True
        if latest != self._at_latest:
            self._at_latest = latest
            self.latestChanged.emit()

    def _short_text(self, message: Message) -> str:
        return " ".join(content_preview(message.get("content", {})).split())[:200]

    def _status(self, message: Message) -> str:
        if not message.get("is_outgoing"):
            return ""
        state = (message.get("sending_state") or {}).get("@type", "")
        if state == "messageSendingStatePending":
            return "pending"
        if state == "messageSendingStateFailed":
            return "failed"
        chat = self._chat()
        if chat and message["id"] <= chat.last_read_outbox_message_id:
            return "read"
        return "sent"

    def _html(self, message: Message) -> str:
        cached = self._html_cache.get(message["id"])
        if cached is not None:
            return cached
        body = message_body(message.get("content", {}))
        result = ""
        if body and body.get("text"):
            result = formatted_to_html(body, self._palette, self._time_spacer(message),
                                       self._emoji_callback(message["id"]))
        self._html_cache[message["id"]] = result
        return result

    def _emoji_callback(self, message_id: int) -> Any:
        return lambda emoji_id: self._emoji_url(emoji_id, message_id)

    def _emoji_url(self, emoji_id: str, message_id: int) -> str:
        if self._emoji is None or not emoji_id:
            return ""
        url = self._emoji.url(emoji_id)
        if url is None:
            self._emoji_waiting.setdefault(emoji_id, set()).add(message_id)
        return url or ""

    def _on_emoji(self, ids: set[str]) -> None:
        history = self._history
        waiting: set[int] = set()
        for emoji_id in ids:
            waiting |= self._emoji_waiting.pop(emoji_id, set())
        if history is None:
            return
        for message_id in waiting:
            row = history.row_of(message_id)
            if row < 0:
                continue
            album = self._album_range(row)
            if album:
                row = album[0]
                for member in history.messages[album[0]:album[1] + 1]:
                    self._html_cache.pop(member["id"], None)
            self._html_cache.pop(message_id, None)
            index = self.index(row)
            self.dataChanged.emit(index, index, [Role.Html, Role.Reactions])

    def _time_spacer(self, message: Message) -> str:
        """Invisible copy of the time label at the end of the text, so the last line leaves room
        for the time drawn over the bubble's bottom-right corner (no extra row needed)."""
        parts = ["edited"] if message.get("edit_date") else []
        parts.append(clock(message.get("date", 0)))
        if message.get("is_outgoing"):
            parts.append(_STATUS_GLYPH["read"])
        text = html.escape("\u2003" + " ".join(parts))
        return f'<span style="font-size:11px;color:{self._time_color}">{text}</span>'

    def _row_changed(self, row: int) -> None:
        if self._history and 0 <= row < len(self._history.messages):
            message_id = self._history.messages[row]["id"]
            for stale in [k for k in self._reactors_asked if k[0] == message_id]:
                self._reactors_asked.discard(stale)  # reactions may have changed
                self._reactors.pop(stale, None)
            self._html_cache.pop(message_id, None)
            self._media_cache.pop(message_id, None)
            index = self.index(row)
            self.dataChanged.emit(index, index)
            album = self._album_range(row)
            if album and row != album[0]:  # the grid on the newest member shows this one
                self._row_changed_plain(album[0])

    def _row_changed_plain(self, row: int) -> None:
        if self._history and 0 <= row < len(self._history.messages):
            self._html_cache.pop(self._history.messages[row]["id"], None)
            index = self.index(row)
            self.dataChanged.emit(index, index)

    def _set_palette(self, **changes: str) -> None:
        palette = Palette(**{**self._palette.__dict__, **changes})
        if palette == self._palette:
            return
        self._palette = palette
        self._html_cache = {}
        self.paletteChanged.emit()
        if self.rowCount() > 0:
            self.dataChanged.emit(self.index(0), self.index(self.rowCount() - 1), [Role.Html])

    def _on_chat_store(self, kind: str, payload: Any) -> None:
        if kind == "chat" and self._history and payload == self._history.chat_id:
            self.chatChanged.emit()  # title etc.
            self.countersChanged.emit()
            if self.rowCount() > 0:  # read receipts
                self.dataChanged.emit(
                    self.index(0), self.index(self.rowCount() - 1), [Role.Status]
                )

    def _on_file(self, file_id: int) -> None:
        history = self._history
        if history is None:
            return
        for message_id in self._file_messages.get(file_id, ()):
            row = history.row_of(message_id)
            if row >= 0:
                album = self._album_range(row)
                if album:
                    row = album[0]
                index = self.index(row)
                self.dataChanged.emit(index, index, MEDIA_ROLES)

    def _on_ai(self, kind: str, payload: Any) -> None:
        history = self._history
        if history is None:
            return
        if kind == "config" and self.rowCount() > 0:  # translation language changed
            self.dataChanged.emit(self.index(0), self.index(self.rowCount() - 1),
                                  [Role.Translation, Role.TranslationState])
            return
        roles = {"transcript": [Role.Transcript, Role.TranscriptState],
                 "translation": [Role.Translation, Role.TranslationState]}.get(kind)
        if roles is None:
            return
        chat_id, message_id = payload
        row = history.row_of(message_id) if chat_id == history.chat_id else -1
        if row >= 0:
            index = self.index(row)
            self.dataChanged.emit(index, index, roles)

    async def _send_file(
        self, chat_id: int, path: str, caption: str = "", reply_to: int = 0,
    ) -> None:
        local = {"@type": "inputFileLocal", "path": path}
        if is_image_path(path):
            # Recent TDLib wraps the file in inputPhoto / inputDocument.
            content: dict[str, Any] = {
                "@type": "inputMessagePhoto",
                "photo": {"@type": "inputPhoto", "photo": local},
            }
        else:
            content = {
                "@type": "inputMessageDocument",
                "document": {"@type": "inputDocument", "document": local},
            }
        if caption.strip():
            content["caption"] = await self._formatted(caption)
        await self._send_content(chat_id, content, reply_to)

    async def _send_content(self, chat_id: int, content: dict[str, Any], reply_to: int) -> None:
        request: dict[str, Any] = {
            "@type": "sendMessage", "chat_id": chat_id, "input_message_content": content}
        if reply_to:
            request["reply_to"] = {"@type": "inputMessageReplyToMessage", "message_id": reply_to}
        try:
            message = await self._client.send(request)
        except TdError as e:
            log.warning("Sending %s failed: %s", content["@type"], e)
            return
        history = self._history
        if (history and history.chat_id == chat_id and message.get("id")
                and history.get(message["id"]) is None):
            history.add(message)

    async def _load_initial(self, history: ChatHistory) -> None:
        """The newest page, or (with unread messages) the history around the last read one,
        however far back: newer pages load as the user scrolls down."""
        boundary = self._unread_after
        if not boundary:
            await history.load_initial()
            return
        await history.load_around(boundary)
        if history is not self._history:
            return
        self._sync_latest()
        unread = [m for m in history.messages if m["id"] > boundary and not m.get("is_outgoing")]
        if not unread:
            await self._load_latest(history)
            return
        self._first_unread = unread[-1]["id"]
        self._row_changed(history.row_of(self._first_unread))
        self.unreadReady.emit(self._first_unread)

    async def _actions(self, chat_id: int, message: Message) -> None:
        content = message.get("content", {})
        has_text = bool((message_body(content) or {}).get("text"))
        actions: dict[str, Any] = {
            "canEdit": self._can_edit_guess(message),
            "canDelete": True,
            "canDeleteForAll": bool(message.get("is_outgoing")),
            "canForward": not is_service(content),
            "canReply": self.canWrite,
            "canCopy": has_text,
            "reactions": [] if is_service(content) else as_items(DEFAULT_REACTIONS[:QUICK]),
            "allReactions": [] if is_service(content) else as_items(DEFAULT_REACTIONS),
            "chosen": [r.key for r in message_reactions(message) if r.chosen],
        }
        ai = self._ai
        props, available, context = await asyncio.gather(
            self._client.send({"@type": "getMessageProperties", "chat_id": chat_id,
                               "message_id": message["id"]}),
            self._client.send({"@type": "getMessageAvailableReactions", "chat_id": chat_id,
                               "message_id": message["id"], "row_size": 8}),
            # Messages Explain / Suggest reply would send: shown in the menu beforehand.
            ai.context_size(chat_id, message["id"]) if ai and ai.is_enabled(chat_id)
            else asyncio.sleep(0, 0),
            return_exceptions=True)
        actions["aiContext"] = context if isinstance(context, int) else 0
        if isinstance(props, dict):
            actions.update(
                canEdit=bool(props.get("can_be_edited")) and _has_text_field(content),
                canDelete=bool(props.get("can_be_deleted_only_for_self")
                               or props.get("can_be_deleted_for_all_users")),
                canDeleteForAll=bool(props.get("can_be_deleted_for_all_users")),
                canForward=bool(props.get("can_be_forwarded")),
                canReply=bool(props.get("can_be_replied", True)) and self.canWrite,
            )
        if isinstance(available, dict):
            keys = available_keys(available)
            actions["reactions"] = as_items(keys[:QUICK])
            actions["allReactions"] = as_items(keys)
        history = self._history
        if history is not None and history.chat_id == chat_id:
            self.actionsReady.emit(message["id"], actions)

    async def _start_edit(self, message: Message) -> None:
        body = message_body(message.get("content", {})) or {
            "@type": "formattedText", "text": "", "entities": []}
        text = body.get("text", "")
        if body.get("entities"):
            try:
                markdown = await self._client.send({"@type": "getMarkdownText", "text": body})
                text = markdown.get("text", text)
            except TdError as e:
                log.debug("getMarkdownText failed, editing plain text: %s", e)
        self.editReady.emit(message["id"], text)

    async def _save_edit(self, chat_id: int, message: Message, text: str) -> None:
        content = message.get("content", {})
        formatted = await self._formatted(text)
        if content.get("@type") == "messageText":
            if not text.strip():
                return
            request: dict[str, Any] = {
                "@type": "editMessageText", "chat_id": chat_id, "message_id": message["id"],
                "reply_markup": None, "input_message_content": {
                    "@type": "inputMessageText", "text": formatted,
                    "link_preview_options": None, "clear_draft": False}}
        else:
            request = {
                "@type": "editMessageCaption", "chat_id": chat_id, "message_id": message["id"],
                "reply_markup": None, "caption": formatted,
                "show_caption_above_media": bool(content.get("show_caption_above_media"))}
        try:
            edited = await self._client.send(request)
        except TdError as e:
            log.warning("Editing a message failed: %s", e)
            return
        history = self._history
        if history and history.chat_id == chat_id and edited.get("id"):
            history.add(edited)

    async def _formatted(self, text: str) -> dict[str, Any]:
        formatted: dict[str, Any] = {"@type": "formattedText", "text": text, "entities": []}
        if not text:
            return formatted
        try:  # **bold**, __italic__, `code`, ```pre```, ~~strike~~, ||spoiler||, [text](url)
            return await self._client.send({"@type": "parseMarkdown", "text": formatted})
        except TdError as e:
            log.debug("parseMarkdown failed, sending plain text: %s", e)
            return formatted

    async def _request(self, request: dict[str, Any], what: str) -> None:
        try:
            await self._client.send(request)
        except TdError as e:
            log.warning("%s failed: %s", what, e)

    async def _run_load(self, history: ChatHistory, load: Any) -> None:
        self._set_loading(True)
        try:
            await load
        finally:
            if history is self._history:
                self._set_loading(False)

    def _set_loading(self, value: bool) -> None:
        if value != self._loading:
            self._loading = value
            self.loadingChanged.emit()

    async def _send(self, chat_id: int, text: str, reply_to: int) -> None:
        formatted = await self._formatted(text)
        request: dict[str, Any] = {
            "@type": "sendMessage",
            "chat_id": chat_id,
            "input_message_content": {"@type": "inputMessageText", "text": formatted,
                                      "link_preview_options": None, "clear_draft": True},
        }
        if reply_to:
            request["reply_to"] = {"@type": "inputMessageReplyToMessage", "message_id": reply_to}
        try:
            message = await self._client.send(request)
        except TdError as e:
            log.warning("sendMessage failed: %s", e)
            return
        history = self._history
        if (history and history.chat_id == chat_id and message.get("id")
                and history.get(message["id"]) is None):
            history.add(message)  # usually already added by updateNewMessage

    def _spawn(self, awaitable: Any) -> None:
        task = asyncio.ensure_future(awaitable)
        task.add_done_callback(_log_failure)


_TELEGRAM_LINK = re.compile(r"^(?:tg:|(?:https?://)?(?:www\.)?(?:t\.me|telegram\.(?:me|dog))/)",
                            re.IGNORECASE)


def _display_size(media: Media) -> tuple[int, int]:
    match media.kind:
        case "sticker":
            return fit(media.width, media.height, 160, 160, min_side=60)
        case "videoNote":
            return 200, 200
        case "photo" | "video" | "animation":
            return fit(media.width, media.height, 320, 320, min_side=120)
    return 0, 0


def _default_name(kind: str) -> str:
    return {"voice": "Voice message", "audio": "Audio", "document": "File"}.get(kind, "")


def is_image_path(path: str) -> bool:
    """Sent as a photo (compressed); GIFs and everything else go as documents."""
    return _suffix(path) in _IMAGE_EXTENSIONS and _suffix(path) != ".gif"


def _has_text_field(content: dict[str, Any]) -> bool:
    """Text messages and media with a caption field (possibly empty) can be edited."""
    return content.get("@type") == "messageText" or "caption" in content


def _now() -> float:
    return datetime.now().timestamp()  # noqa: DTZ005


def _suffix(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:].lower() if dot > path.rfind("/") else ""


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else path


def _day(message: Message) -> object:
    return datetime.fromtimestamp(message.get("date", 0)).date()  # noqa: DTZ006


def _log_failure(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("Message model task failed", exc_info=task.exception())
