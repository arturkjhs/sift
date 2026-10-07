"""The message input of the open chat: cloud drafts, staged attachments, our typing status.

Drafts: QML reports every change with setDraft(); it is saved to TDLib (setChatDraftMessage)
after a pause and when switching chats, so it syncs with other devices. A draft changed on
another device replaces the input only if the user hasn't typed since the last sync.

Attachments (file dialog, drag-and-drop, pasted images or files) are staged first, so the user
can add a caption; sendStaged() sends them through MessageListModel.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QImage

from ..store.chats import ChatStore, draft_reply_to, draft_text
from ..store.files import AUTO_PRIORITY
from ..store.link_preview import parse as parse_link_preview
from ..store.media import human_size
from ..td.client import TdClient, TdError
from .messages import MessageListModel, is_image_path

log = logging.getLogger(__name__)

DRAFT_DELAY = 1.5  # seconds of no typing before the draft is saved
TYPING_INTERVAL = 5.0  # Telegram shows a typing action for ~6 s
PASTE_KEEP_DAYS = 7
PREVIEW_DELAY = 0.6  # seconds of no typing before asking TDLib for the link's preview
_URL = re.compile(r"(?:https?://|www\.|t\.me/)\S+|\b[\w-]+\.(?:com|org|net|io|dev|cz|ru|ua)"
                  r"(?:/\S*)?\b", re.IGNORECASE)


class ComposerModel(QObject):
    stagedChanged = Signal()
    linkPreviewChanged = Signal()
    remoteDraft = Signal(str, "QVariant")  # text, reply-to id: replace the input

    def __init__(
        self, client: TdClient, chats: ChatStore, messages: MessageListModel,
        paste_dir: Path | None = None, parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._chats = chats
        self._messages = messages
        self._paste_dir = paste_dir
        self._chat_id = 0
        self._text = ""
        self._reply_to = 0
        self._synced: tuple[str, int] = ("", 0)  # what TDLib has for the current chat
        self._save_task: asyncio.Task[Any] | None = None
        self._last_typing = 0.0
        self._staged: list[dict[str, Any]] = []
        self._preview: dict[str, Any] = {}  # link preview of the input, as in the feed
        self._preview_off = False  # the user removed it: send without a preview
        self._preview_task: asyncio.Task[Any] | None = None
        self._preview_url = ""
        self._preview_image = 0  # file id of the preview's picture, while it downloads
        chats.files.subscribe(self._on_file)
        self._tasks: set[asyncio.Task[Any]] = set()
        messages.chatChanged.connect(self._on_chat_changed)
        chats.subscribe(self._on_chats)
        _clean_old_pastes(paste_dir)

    # --- drafts -----------------------------------------------------------------------------

    @Property(str, notify=remoteDraft)
    def draftText(self) -> str:
        """Saved draft of the open chat: QML puts it into the input when a chat opens."""
        chat = self._chats.chats.get(int(self._messages.chatId or 0))
        return draft_text(chat.draft) if chat else ""

    @Property("QVariant", notify=remoteDraft)
    def draftReplyTo(self) -> int:
        chat = self._chats.chats.get(int(self._messages.chatId or 0))
        return draft_reply_to(chat.draft) if chat else 0

    @Slot(str, "QVariant")
    def setDraft(self, text: str, reply_to: Any = 0) -> None:
        if not self._chat_id:
            return
        reply_to = int(reply_to or 0)
        typed = text != self._text and bool(text.strip())
        self._text, self._reply_to = text, reply_to
        if typed:
            self._send_typing()
        self._cancel_save()
        if (text.strip(), reply_to) != self._synced:
            self._save_task = self._spawn(self._save_later(self._chat_id, text, reply_to))
        self._check_link(text)

    @Slot()
    def sent(self) -> None:
        """The input was sent: TDLib clears the draft itself (clear_draft)."""
        self._cancel_save()
        self._text, self._reply_to = "", 0
        self._synced = ("", 0)
        self._reset_preview()

    def flush(self) -> asyncio.Future[Any] | None:
        """Save a pending draft now (switching chats, quitting)."""
        if self._save_task is None or self._save_task.done():
            return None
        self._save_task.cancel()
        self._save_task = None
        return self._spawn(self._save(self._chat_id, self._text, self._reply_to))

    async def close(self) -> None:
        pending = self.flush()
        if pending is not None:
            await asyncio.wait([pending], timeout=2)

    def _on_chat_changed(self) -> None:
        chat_id = int(self._messages.chatId or 0)
        if chat_id == self._chat_id:
            return
        self.flush()
        self._chat_id = chat_id
        self._last_typing = 0.0
        chat = self._chats.chats.get(chat_id)
        self._text = draft_text(chat.draft) if chat else ""
        self._reply_to = draft_reply_to(chat.draft) if chat else 0
        self._synced = (self._text.strip(), self._reply_to)
        self.clearStaged()
        self._reset_preview()

    def _on_chats(self, kind: str, payload: Any) -> None:
        if kind != "chat" or payload != self._chat_id or not self._chat_id:
            return
        chat = self._chats.chats[self._chat_id]
        remote = (draft_text(chat.draft), draft_reply_to(chat.draft))
        if (remote[0].strip(), remote[1]) == self._synced:
            return  # our own save coming back, or nothing changed
        if (self._text.strip(), self._reply_to) != self._synced:
            return  # the user is typing: their text wins, it will overwrite the cloud draft
        self._text, self._reply_to = remote
        self._synced = (remote[0].strip(), remote[1])
        self.remoteDraft.emit(remote[0], remote[1])

    async def _save_later(self, chat_id: int, text: str, reply_to: int) -> None:
        await asyncio.sleep(DRAFT_DELAY)
        await self._save(chat_id, text, reply_to)

    async def _save(self, chat_id: int, text: str, reply_to: int) -> None:
        draft: dict[str, Any] | None = None
        if text.strip():
            draft = {
                "@type": "draftMessage",
                "reply_to": ({"@type": "inputMessageReplyToMessage", "message_id": reply_to}
                             if reply_to else None),
                "date": 0,
                "content": {"@type": "draftMessageContentText", "text": {
                    "@type": "formattedText", "text": text, "entities": []},
                    "link_preview_options": None},
                "effect_id": 0,
                "suggested_post_info": None,
            }
        if chat_id == self._chat_id:
            self._synced = (text.strip(), reply_to if draft else 0)
        try:
            await self._client.send({"@type": "setChatDraftMessage", "chat_id": chat_id,
                                     "topic_id": None, "draft_message": draft})
        except TdError as e:
            log.warning("Saving the draft failed: %s", e)

    def _cancel_save(self) -> None:
        if self._save_task is not None:
            self._save_task.cancel()
            self._save_task = None

    def _send_typing(self) -> None:
        now = time.monotonic()
        if now - self._last_typing < TYPING_INTERVAL:
            return
        self._last_typing = now
        self._spawn(self._request({
            "@type": "sendChatAction", "chat_id": self._chat_id, "topic_id": None,
            "business_connection_id": "", "action": {"@type": "chatActionTyping"}}))

    # --- link preview -----------------------------------------------------------------------

    @Property("QVariantMap", notify=linkPreviewChanged)
    def linkPreview(self) -> dict[str, Any]:
        """Preview of the first link in the input ({} if none, or the user removed it)."""
        return {} if self._preview_off else dict(self._preview)

    @Property(bool, notify=linkPreviewChanged)
    def linkPreviewOff(self) -> bool:
        return self._preview_off

    @Slot()
    def removeLinkPreview(self) -> None:
        self._preview_off = True
        self.linkPreviewChanged.emit()

    @Slot(result="QVariantMap")
    def sendOptions(self) -> dict[str, Any]:
        """For MessageListModel.sendMessage: what the composer decided about this message."""
        return {"noPreview": self._preview_off}

    def _check_link(self, text: str) -> None:
        match = _URL.search(text)
        url = match.group(0) if match else ""
        if url == self._preview_url:
            return
        self._preview_url = url
        if self._preview_task is not None:
            self._preview_task.cancel()
            self._preview_task = None
        if not url:
            if self._preview:
                self._preview = {}
                self.linkPreviewChanged.emit()
            return
        self._preview_task = self._spawn(self._fetch_preview(text, url))

    async def _fetch_preview(self, text: str, url: str) -> None:
        await asyncio.sleep(PREVIEW_DELAY)
        try:
            raw = await self._client.send({
                "@type": "getLinkPreview",
                "text": {"@type": "formattedText", "text": text, "entities": []},
                "link_preview_options": None})
        except TdError as e:
            log.debug("No link preview for %s: %s", url, e)
            raw = None
        if url != self._preview_url:
            return
        preview = parse_link_preview(raw)
        files = self._chats.files
        image = ""
        if preview is not None and preview.image:
            files.register(preview.image)
            image_id = preview.image["id"]
            image = files.url("media", image_id) if files.path(image_id) else ""
            if not image:
                self._preview_image = image_id  # _on_file fills it in
                files.download(image_id, AUTO_PRIORITY)
        self._preview = {} if preview is None else {
            "url": preview.url, "site": preview.site, "title": preview.title,
            "text": preview.description, "image": image}
        self.linkPreviewChanged.emit()

    def _on_file(self, file_id: int) -> None:
        if file_id != self._preview_image or not self._preview:
            return
        if self._chats.files.path(file_id):
            self._preview_image = 0
            self._preview["image"] = self._chats.files.url("media", file_id)
            self.linkPreviewChanged.emit()

    def _reset_preview(self) -> None:
        if self._preview_task is not None:
            self._preview_task.cancel()
            self._preview_task = None
        changed = bool(self._preview) or self._preview_off
        self._preview, self._preview_off, self._preview_url = {}, False, ""
        if changed:
            self.linkPreviewChanged.emit()

    # --- attachments ------------------------------------------------------------------------

    @Property("QVariantList", notify=stagedChanged)
    def staged(self) -> list[dict[str, Any]]:
        return list(self._staged)

    @Slot("QVariantList")
    def stage(self, urls: list[Any]) -> None:
        for url in urls:
            path = url.toLocalFile() if isinstance(url, QUrl) else QUrl(str(url)).toLocalFile()
            self._stage_path(path or str(url))
        self.stagedChanged.emit()

    @Slot(result=bool)
    def pasteClipboard(self) -> bool:
        """Cmd/Ctrl+V: stage an image or copied files. False: let the text field paste text."""
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None:
            return False
        files = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()] if mime.hasUrls() else []
        if files:
            for path in files:
                self._stage_path(path)
            self.stagedChanged.emit()
            return True
        if mime.hasImage() and not mime.text().strip():  # e.g. a spreadsheet copies both
            image = QGuiApplication.clipboard().image()
            path = self._save_pasted(image)
            if path:
                self._stage_path(path, name="Pasted image.png")
                self.stagedChanged.emit()
                return True
        return False

    @Slot(int)
    def unstage(self, index: int) -> None:
        if 0 <= index < len(self._staged):
            del self._staged[index]
            self.stagedChanged.emit()

    @Slot()
    def clearStaged(self) -> None:
        if self._staged:
            self._staged = []
            self.stagedChanged.emit()

    @Slot(str, "QVariant")
    def sendStaged(self, caption: str, reply_to: Any = 0) -> None:
        """Send all staged files; the caption goes with the first one."""
        reply_to = int(reply_to or 0)
        self._messages.send_files([
            (item["path"], caption if index == 0 else "", reply_to if index == 0 else 0)
            for index, item in enumerate(self._staged)])
        self.clearStaged()

    def _stage_path(self, path: str, name: str = "") -> None:
        file = Path(path)
        if not file.is_file() or any(item["path"] == path for item in self._staged):
            return
        image = is_image_path(path)
        self._staged.append({
            "path": path,
            "name": name or file.name,
            "size": human_size(file.stat().st_size),
            "isImage": image,
            "source": QUrl.fromLocalFile(path).toString() if image else "",
        })

    def _save_pasted(self, image: QImage) -> str:
        if image.isNull() or self._paste_dir is None:
            return ""
        try:
            self._paste_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("Can't save a pasted image: %s", e)
            return ""
        path = self._paste_dir / f"pasted-{time.strftime('%Y%m%d-%H%M%S')}-{id(image)}.png"
        return str(path) if image.save(str(path), "PNG") else ""

    # --- helpers ----------------------------------------------------------------------------

    async def _request(self, request: dict[str, Any]) -> None:
        try:
            await self._client.send(request)
        except TdError as e:
            log.debug("%s failed: %s", request["@type"], e)

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("Composer task failed", exc_info=task.exception())


def _clean_old_pastes(paste_dir: Path | None) -> None:
    """Pasted images must outlive their upload (TDLib reads them later); a week is plenty."""
    if paste_dir is None or not paste_dir.is_dir():
        return
    cutoff = time.time() - PASTE_KEEP_DAYS * 86400
    for path in paste_dir.glob("pasted-*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
