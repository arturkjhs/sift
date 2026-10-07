"""Qt model of the GIF tab: saved GIFs, or search results from the @gif inline bot. Previews
are static thumbnails (or the blurred minithumbnail) downloaded as cells show up."""

from __future__ import annotations

import asyncio
import logging
from enum import IntEnum, auto
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
    Slot,
)

from ..store.files import AUTO_PRIORITY, FileManager
from ..store.media import extract
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

SEARCH_BOT = "gif"
SEARCH_DELAY = 0.35  # seconds of no typing before asking the bot
AnyIndex = QModelIndex | QPersistentModelIndex


class Role(IntEnum):
    Source = Qt.ItemDataRole.UserRole + 1
    Ratio = auto()  # width / height


class GifModel(QAbstractListModel):
    queryChanged = Signal()
    loadingChanged = Signal()

    def __init__(self, client: TdClient, files: FileManager, parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._files = files
        self._query = ""
        self._items: list[dict[str, Any]] = []  # TDLib animations
        self._saved: list[dict[str, Any]] = []
        self._rows_by_file: dict[int, list[int]] = {}
        self._loading = False
        self._bot_id = 0
        self._search_task: asyncio.Task[Any] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        files.subscribe(self._on_file)

    # --- QML API ----------------------------------------------------------------------------

    def _get_query(self) -> str:
        return self._query

    def _set_query(self, query: str) -> None:
        if query == self._query:
            return
        self._query = query
        self.queryChanged.emit()
        if self._search_task is not None:
            self._search_task.cancel()
        if query.strip():
            self._search_task = self._spawn(self._search(query.strip()))
        else:
            self._show(self._saved)

    query = Property(str, _get_query, _set_query, notify=queryChanged)

    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        return self._loading

    @Slot()
    def load(self) -> None:
        """Saved GIFs (when the tab opens)."""
        self._spawn(self._load_saved())

    @Slot(int, result="QVariantMap")
    def animation(self, row: int) -> dict[str, Any]:
        return dict(self._items[row]) if 0 <= row < len(self._items) else {}

    # --- QAbstractListModel -----------------------------------------------------------------

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self) -> dict[int, QByteArray]:
        return {Role.Source: QByteArray(b"source"), Role.Ratio: QByteArray(b"ratio")}

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        animation = self._items[index.row()]
        if role == Role.Ratio:
            width, height = animation.get("width", 0), animation.get("height", 0)
            return width / height if width and height else 1.0
        if role == Role.Source:
            return self._source(index.row(), animation)
        return None

    # --- internals --------------------------------------------------------------------------

    def _source(self, row: int, animation: dict[str, Any]) -> str:
        media = extract({"@type": "messageAnimation", "animation": animation})
        preview = media.preview if media else None
        if preview is None:
            return ""
        file_id = preview["id"]
        self._files.register(preview)
        if self._files.path(file_id):
            return self._files.url("media", file_id)
        self._rows_by_file.setdefault(file_id, []).append(row)
        self._files.download(file_id, AUTO_PRIORITY)
        if media and media.minithumbnail:
            self._files.minithumbnails[file_id] = media.minithumbnail
            return self._files.url("mini", file_id)
        return ""

    async def _load_saved(self) -> None:
        try:
            found = await self._client.send({"@type": "getSavedAnimations"})
        except TdError as e:
            log.info("getSavedAnimations failed: %s", e)
            return
        self._saved = [a for a in found.get("animations") or [] if a]
        if not self._query.strip():
            self._show(self._saved)

    async def _search(self, query: str) -> None:
        await asyncio.sleep(SEARCH_DELAY)
        self._set_loading(True)
        try:
            if not self._bot_id:
                bot = await self._client.send({"@type": "searchPublicChat",
                                               "username": SEARCH_BOT})
                self._bot_id = int((bot.get("type") or {}).get("user_id") or bot.get("id") or 0)
            found = await self._client.send({
                "@type": "getInlineQueryResults", "bot_user_id": self._bot_id, "chat_id": 0,
                "user_location": None, "query": query, "offset": ""}, timeout=20)
        except (TdError, TimeoutError) as e:
            log.info("GIF search failed: %s", e)
            found = {}
        finally:
            self._set_loading(False)
        if query != self._query.strip():
            return
        self._show([r["animation"] for r in found.get("results") or []
                    if r.get("@type") == "inlineQueryResultAnimation" and r.get("animation")])

    def _show(self, items: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self._rows_by_file = {}
        self.endResetModel()

    def _set_loading(self, value: bool) -> None:
        if value != self._loading:
            self._loading = value
            self.loadingChanged.emit()

    def _on_file(self, file_id: int) -> None:
        if not self._files.path(file_id):
            return
        for row in self._rows_by_file.pop(file_id, []):
            if row < len(self._items):
                index = self.index(row)
                self.dataChanged.emit(index, index, [Role.Source])

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task
