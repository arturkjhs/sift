"""Qt model of the open chat's messages (row 0 = newest; the view is BottomToTop)."""

from __future__ import annotations

import asyncio
import html
import logging
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
from ..store.chats import ChatStore
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
from ..store.media import Media, decode_waveform, duration_text, extract, fit, human_size
from ..store.richtext import Palette, formatted_to_html
from ..store.users import UserStore
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

AVATAR_COLORS = 7
GROUP_WINDOW_SECONDS = 5 * 60
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


# Roles that depend on file state (refreshed when a file of the message changes).
MEDIA_ROLES = [Role.MediaSource, Role.FileInfo, Role.FileState, Role.FileProgress,
               Role.SenderAvatar]
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_AUTO_OPEN_KINDS = {"photo", "video", "animation", "videoNote"}


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

    def history_remove(self, row: int, count: int, commit: Commit) -> None:
        if not self._current():
            commit()
            return
        self._model.beginRemoveRows(QModelIndex(), row, row + count - 1)
        message_id = self._ref[0].messages[row]["id"]
        commit()
        self._model._html_cache.pop(message_id, None)
        self._model.endRemoveRows()

    def history_changed(self, row: int) -> None:
        if self._current():
            self._model._row_changed(row)


class MessageListModel(QAbstractListModel):
    chatChanged = Signal()
    loadingChanged = Signal()
    paletteChanged = Signal()
    jumpReady = Signal(int)  # row of a message requested by jumpTo(), once it's loaded

    def __init__(
        self, client: TdClient, chats: ChatStore, users: UserStore,
        ai: AiService | None = None, parent: Any = None,
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
        self._open_when_ready: set[int] = set()
        self._files: FileManager = chats.files
        self._files.subscribe(self._on_file)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        self._palette = Palette(mono=mono)
        self._time_color = "#00000000"
        chats.subscribe(self._on_chat_store)
        self._ai = ai
        if ai is not None:
            ai.subscribe(self._on_ai)

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
        history = ChatHistory(self._client, chat_id, _Adapter(self, ref))
        ref.append(history)
        self.beginResetModel()
        self._history = history
        self._viewed = set()
        self._html_cache = {}
        self._media_cache = {}
        self._file_messages = {}
        self.endResetModel()
        self.chatChanged.emit()
        self._spawn(self._client.send({"@type": "openChat", "chat_id": chat_id}))
        self._spawn(self._run_load(history, history.load_initial()))

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

    @Slot(str, "QVariant")
    def send(self, text: str, reply_to: Any = 0) -> None:
        history = self._history
        if history is None or not text.strip():
            return
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

    @Slot("QVariant", result=int)
    def rowOf(self, message_id: Any) -> int:
        return self._history.row_of(int(message_id)) if self._history else -1

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
            found = await history.load_until(message_id)
            if found and history is self._history:
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
        file_id = media.file["id"]
        state = self._files.get(file_id)
        status = state.status if state else "remote"
        if status == "ready":
            self.openFile(file_id)
        elif status == "downloading":
            self._open_when_ready.discard(file_id)
            self._files.cancel(file_id)
        elif status == "remote":
            if media.kind in _AUTO_OPEN_KINDS:
                self._open_when_ready.add(file_id)
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

    @Slot("QVariantList")
    def sendFiles(self, urls: list[Any]) -> None:
        history = self._history
        if history is None:
            return
        for url in urls:
            path = url.toLocalFile() if isinstance(url, QUrl) else QUrl(str(url)).toLocalFile()
            if not path:
                path = str(url)
            self._spawn(self._send_file(history.chat_id, path))

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
                    self._same_group(message, self._neighbor(row + 1)))
            case Role.GroupBottom:
                return not self._same_group(message, self._neighbor(row - 1))
            case Role.ShowAvatar:
                return (self._is_group_chat() and not message.get("is_outgoing")
                        and not self._same_group(message, self._neighbor(row - 1)))
            case Role.Html:
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
            case Role.DayLabel:
                older = self._neighbor(row + 1)
                if older is None:
                    return day_label(message.get("date", 0)) if history.reached_start else ""
                if _day(older) != _day(message):
                    return day_label(message.get("date", 0))
                return ""
        return self._media_data(message, role)

    def _media_data(self, message: Message, role: int) -> Any:
        media = self._media(message)
        if media is None:
            return {Role.MediaKind: "", Role.MediaSource: "", Role.MediaWidth: 0,
                    Role.MediaHeight: 0, Role.FileId: 0, Role.FileName: "", Role.FileInfo: "",
                    Role.FileState: "", Role.FileProgress: 0.0, Role.Duration: "",
                    Role.Waveform: [], Role.StickerEmoji: ""}.get(role)
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
        return None

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
                return f"image://tg/sticker/{preview_id}"
            if media.kind == "videoNote":
                return f"image://tg/avatar/{preview_id}"
            return f"image://tg/media/{preview_id}"
        self._files.download(preview_id, AUTO_PRIORITY)
        if preview_id in self._files.minithumbnails and media.kind != "sticker":
            return f"image://tg/mini/{preview_id}"
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
            return f"image://tg/avatar/{file_id}"
        self._files.download(file_id, AUTO_PRIORITY)
        return ""

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
            result = formatted_to_html(body, self._palette, self._time_spacer(message))
        self._html_cache[message["id"]] = result
        return result

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
            self._html_cache.pop(message_id, None)
            self._media_cache.pop(message_id, None)
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
            if self.rowCount() > 0:  # read receipts
                self.dataChanged.emit(
                    self.index(0), self.index(self.rowCount() - 1), [Role.Status]
                )

    def _on_file(self, file_id: int) -> None:
        if file_id in self._open_when_ready and self._files.path(file_id):
            self._open_when_ready.discard(file_id)
            self.openFile(file_id)
        history = self._history
        if history is None:
            return
        for message_id in self._file_messages.get(file_id, ()):
            row = history.row_of(message_id)
            if row >= 0:
                index = self.index(row)
                self.dataChanged.emit(index, index, MEDIA_ROLES)

    def _on_ai(self, kind: str, payload: Any) -> None:
        history = self._history
        if kind != "transcript" or history is None:
            return
        chat_id, message_id = payload
        row = history.row_of(message_id) if chat_id == history.chat_id else -1
        if row >= 0:
            index = self.index(row)
            self.dataChanged.emit(index, index, [Role.Transcript, Role.TranscriptState])

    async def _send_file(self, chat_id: int, path: str) -> None:
        local = {"@type": "inputFileLocal", "path": path}
        if _suffix(path) in _IMAGE_EXTENSIONS and _suffix(path) != ".gif":
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
        try:
            message = await self._client.send(
                {"@type": "sendMessage", "chat_id": chat_id, "input_message_content": content})
        except TdError as e:
            log.warning("Sending %s failed: %s", path, e)
            return
        history = self._history
        if history and history.chat_id == chat_id and history.get(message["id"]) is None:
            history.add(message)

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
        if history and history.chat_id == chat_id and history.get(message["id"]) is None:
            history.add(message)

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
        formatted: dict[str, Any] = {"@type": "formattedText", "text": text, "entities": []}
        try:  # **bold**, __italic__, `code`, ```pre```, ~~strike~~, ||spoiler||, [text](url)
            formatted = await self._client.send({"@type": "parseMarkdown", "text": formatted})
        except TdError as e:
            log.debug("parseMarkdown failed, sending plain text: %s", e)
        request: dict[str, Any] = {
            "@type": "sendMessage",
            "chat_id": chat_id,
            "input_message_content": {"@type": "inputMessageText", "text": formatted},
        }
        if reply_to:
            request["reply_to"] = {"@type": "inputMessageReplyToMessage", "message_id": reply_to}
        try:
            message = await self._client.send(request)
        except TdError as e:
            log.warning("sendMessage failed: %s", e)
            return
        history = self._history
        if history and history.chat_id == chat_id and history.get(message["id"]) is None:
            history.add(message)  # usually already added by updateNewMessage

    def _spawn(self, awaitable: Any) -> None:
        task = asyncio.ensure_future(awaitable)
        task.add_done_callback(_log_failure)


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
