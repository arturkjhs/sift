"""Full-window photo and video viewer of the open chat.

open(message_id) takes a snapshot of the photos and videos in the loaded history (album members
included), oldest first, and shows that message; next()/prev() walk the snapshot. The shown
item's full file downloads with user priority (its neighbours with low priority); until then
the preview image is shown with the progress.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ..store.files import AUTO_PRIORITY, USER_PRIORITY, FileManager
from ..store.format import message_body
from ..store.media import Media, extract
from .messages import MessageListModel

log = logging.getLogger(__name__)

VIEWABLE = {"photo", "video", "animation", "videoNote"}


class ViewerModel(QObject):
    changed = Signal()

    def __init__(self, messages: MessageListModel, files: FileManager,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self._messages = messages
        self._files = files
        self._items: list[tuple[dict[str, Any], Media]] = []
        self._index = -1
        files.subscribe(self._on_file)
        messages.chatChanged.connect(self._on_chat_changed)

    # --- QML API ----------------------------------------------------------------------------

    @Slot("QVariant")
    def open(self, message_id: Any) -> None:
        history = self._messages._history
        if history is None:
            return
        items = []
        for message in reversed(history.messages):  # oldest first
            media = extract(message.get("content", {}))
            if media is not None and media.kind in VIEWABLE and media.file:
                items.append((message, media))
        index = next((i for i, (m, _) in enumerate(items) if m["id"] == int(message_id or 0)), -1)
        if index < 0:
            return
        self._items = items
        self._show(index)

    @Slot()
    def close(self) -> None:
        if self._index >= 0:
            self._index = -1
            self._items = []
            self.changed.emit()

    @Slot()
    def next(self) -> None:
        if 0 <= self._index < len(self._items) - 1:
            self._show(self._index + 1)

    @Slot()
    def prev(self) -> None:
        if self._index > 0:
            self._show(self._index - 1)

    @Property(bool, notify=changed)
    def active(self) -> bool:
        return self._index >= 0

    @Property(int, notify=changed)
    def index(self) -> int:
        return self._index

    @Property(int, notify=changed)
    def count(self) -> int:
        return len(self._items)

    @Property(str, notify=changed)
    def kind(self) -> str:
        media = self._media()
        return media.kind if media else ""

    @Property("QVariant", notify=changed)
    def messageId(self) -> int:
        message = self._message()
        return message["id"] if message else 0

    @Property(str, notify=changed)
    def source(self) -> str:
        """Full file as a file:// URL once downloaded, else ""."""
        path = self._path()
        return QUrl.fromLocalFile(path).toString() if path else ""

    @Property(str, notify=changed)
    def preview(self) -> str:
        media = self._media()
        if media is None or media.preview is None:
            return ""
        self._files.register(media.preview)
        if self._files.path(media.preview["id"]):
            return self._files.url("full", media.preview["id"])
        self._files.download(media.preview["id"], USER_PRIORITY)
        return ""

    @Property(int, notify=changed)
    def mediaWidth(self) -> int:
        media = self._media()
        return media.width if media else 0

    @Property(int, notify=changed)
    def mediaHeight(self) -> int:
        media = self._media()
        return media.height if media else 0

    @Property(float, notify=changed)
    def progress(self) -> float:
        media = self._media()
        state = self._files.get(media.file["id"]) if media and media.file else None
        return state.progress if state else 0.0

    @Property(bool, notify=changed)
    def ready(self) -> bool:
        return bool(self._path())

    @Property(str, notify=changed)
    def caption(self) -> str:
        message = self._message()
        body = message_body(message.get("content", {})) if message else None
        return (body or {}).get("text", "")

    @Property(str, notify=changed)
    def sender(self) -> str:
        message = self._message()
        return self._messages._sender_name(message) if message else ""

    @Property(str, notify=changed)
    def date(self) -> str:
        message = self._message()
        if not message:
            return ""
        when = datetime.fromtimestamp(message.get("date", 0))  # noqa: DTZ006
        return when.strftime("%d %b %Y, %H:%M")

    @Slot()
    def openExternally(self) -> None:
        path = self._path()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    @Slot()
    def showInFolder(self) -> None:
        media = self._media()
        if media and media.file:
            self._messages.showInFolder(media.file["id"])

    @Slot("QVariant", result=bool)
    def saveAs(self, url: Any) -> bool:
        path = self._path()
        target = url.toLocalFile() if isinstance(url, QUrl) else QUrl(str(url)).toLocalFile()
        if not path or not target:
            return False
        try:
            shutil.copyfile(path, target)
        except OSError as e:
            log.warning("Saving %s failed: %s", target, e)
            return False
        return True

    @Property(str, notify=changed)
    def suggestedName(self) -> str:
        media = self._media()
        if media is None:
            return ""
        if media.file_name:
            return media.file_name
        message = self._message() or {}
        extension = ".jpg" if media.kind == "photo" else ".mp4"
        return f"{media.kind}-{message.get('id', 0)}{extension}"

    # --- internals --------------------------------------------------------------------------

    def _message(self) -> dict[str, Any] | None:
        return self._items[self._index][0] if 0 <= self._index < len(self._items) else None

    def _media(self) -> Media | None:
        return self._items[self._index][1] if 0 <= self._index < len(self._items) else None

    def _path(self) -> str:
        media = self._media()
        if media is None or not media.file:
            return ""
        return self._files.path(media.file["id"]) or ""

    def _show(self, index: int) -> None:
        self._index = index
        for offset, priority in ((0, USER_PRIORITY), (1, AUTO_PRIORITY), (-1, AUTO_PRIORITY)):
            near = index + offset
            if 0 <= near < len(self._items):
                media = self._items[near][1]
                if offset == 0 or media.kind == "photo":  # videos are big: no prefetch
                    self._files.register(media.file)
                    self._files.download(media.file["id"], priority)
        self.changed.emit()

    def _on_file(self, file_id: int) -> None:
        media = self._media()
        if media is None:
            return
        ids = {f["id"] for f in (media.file, media.preview) if f}
        if file_id in ids:
            self.changed.emit()

    def _on_chat_changed(self) -> None:
        history = self._messages._history
        if self._index >= 0 and (history is None or self._items and
                                 self._items[0][0].get("chat_id") != history.chat_id):
            self.close()
