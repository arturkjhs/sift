"""Qt model of the sticker picker: the stickers of the selected set (or recent), plus the set
tabs. Previews download lazily, like everywhere else: only for cells QML actually shows."""

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
from ..store.stickers import Sticker, StickerStore

log = logging.getLogger(__name__)

RECENT = "recent"
AnyIndex = QModelIndex | QPersistentModelIndex


class Role(IntEnum):
    Source = Qt.ItemDataRole.UserRole + 1
    Emoji = auto()


def preview_file_id(sticker: Sticker) -> int | None:
    """Static image to show: the WebP itself, or the thumbnail of a TGS/WebM sticker."""
    media = extract({"@type": "messageSticker", "sticker": sticker})
    return media.preview["id"] if media and media.preview else None


class StickerModel(QAbstractListModel):
    setsChanged = Signal()
    currentSetChanged = Signal()
    loadingChanged = Signal()

    def __init__(self, store: StickerStore, files: FileManager, parent: Any = None) -> None:
        super().__init__(parent)
        self._store = store
        self._files = files
        self._current = RECENT
        self._items: list[Sticker] = []
        self._rows_by_file: dict[int, list[int]] = {}
        self._set_thumbs: set[int] = set()
        store.subscribe(self._on_store)
        files.subscribe(self._on_file)

    # --- QML API ----------------------------------------------------------------------------

    @Slot()
    def load(self) -> None:
        """Called when the picker opens; loads sets and recent stickers once."""
        if not self._store.loaded:
            self.loadingChanged.emit()
            task = asyncio.ensure_future(self._store.load())
            task.add_done_callback(lambda _t: self.loadingChanged.emit())

    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        return not self._store.loaded or (
            self._current != RECENT and self._store.stickers(self._current) is None)

    @Property("QVariantList", notify=setsChanged)
    def sets(self) -> list[dict[str, str]]:
        tabs = [{"key": RECENT, "title": "Recently used", "source": ""}] if self._store.recent \
            else []
        for info in self._store.sets:
            covers = info.get("covers") or []
            source = self._source(covers[0], track_set=True) if covers else ""
            tabs.append({"key": str(info["id"]), "title": info.get("title", ""),
                         "source": source})
        return tabs

    def _get_current(self) -> str:
        return self._current

    def _set_current(self, key: str) -> None:
        if key != self._current:
            self._current = key
            self.currentSetChanged.emit()
            self._refresh()

    currentSet = Property(str, _get_current, _set_current, notify=currentSetChanged)

    @Slot(int, result="QVariantMap")
    def sticker(self, row: int) -> Sticker:
        """TDLib's sticker object for messages.sendSticker()."""
        return self._items[row] if 0 <= row < len(self._items) else {}

    # --- QAbstractListModel -----------------------------------------------------------------

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self) -> dict[int, QByteArray]:
        return {Role.Source: QByteArray(b"source"), Role.Emoji: QByteArray(b"emoji")}

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        sticker = self._items[index.row()]
        if role == Role.Source:
            return self._source(sticker)
        if role == Role.Emoji:
            return sticker.get("emoji", "")
        return None

    # --- internals --------------------------------------------------------------------------

    def _source(self, sticker: Sticker, track_set: bool = False) -> str:
        file_id = preview_file_id(sticker)
        if file_id is None:
            return ""
        if track_set:
            self._set_thumbs.add(file_id)
        if self._files.path(file_id):
            return f"image://tg/sticker/{file_id}"
        self._files.download(file_id, AUTO_PRIORITY)
        return ""

    def _refresh(self) -> None:
        if self._current == RECENT:
            items = self._store.recent
        else:
            items = self._store.stickers(self._current) or []
        self.beginResetModel()
        self._items = list(items)
        self._rows_by_file = {}
        for row, sticker in enumerate(self._items):
            file_id = preview_file_id(sticker)
            if file_id is not None:
                self._rows_by_file.setdefault(file_id, []).append(row)
        self.endResetModel()
        self.loadingChanged.emit()

    def _on_store(self, what: str) -> None:
        if what in ("sets", "recent"):
            self.setsChanged.emit()
        if what == "loaded":
            self.loadingChanged.emit()
            if self._current == RECENT and not self._store.recent and self._store.sets:
                self._set_current(str(self._store.sets[0]["id"]))  # no recent: first set
            return
        if what == "recent" and self._current == RECENT or what == f"set:{self._current}":
            self._refresh()

    def _on_file(self, file_id: int) -> None:
        if file_id in self._set_thumbs and self._files.path(file_id):
            self._set_thumbs.discard(file_id)
            self.setsChanged.emit()
        for row in self._rows_by_file.get(file_id, ()):
            index = self.index(row)
            self.dataChanged.emit(index, index, [Role.Source])
