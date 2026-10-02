"""Qt model of the emoji picker: one category's emoji, or search results."""

from __future__ import annotations

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

from ..store.emoji import RECENT, EmojiCatalog

AnyIndex = QModelIndex | QPersistentModelIndex


class Role(IntEnum):
    Emoji = Qt.ItemDataRole.UserRole + 1
    Name = auto()


class EmojiModel(QAbstractListModel):
    categoryChanged = Signal()
    queryChanged = Signal()

    def __init__(self, catalog: EmojiCatalog, parent: Any = None) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._category = RECENT if catalog.recent else catalog.categories[1].key
        self._query = ""
        self._items = catalog.items(self._category)

    @Property("QVariantList", constant=True)
    def categories(self) -> list[dict[str, str]]:
        return [{"key": c.key, "title": c.title, "glyph": c.glyph}
                for c in self._catalog.categories]

    def _get_category(self) -> str:
        return self._category

    def _set_category(self, category: str) -> None:
        if category != self._category:
            self._category = category
            self.categoryChanged.emit()
            self._refresh()

    category = Property(str, _get_category, _set_category, notify=categoryChanged)

    def _get_query(self) -> str:
        return self._query

    def _set_query(self, query: str) -> None:
        if query != self._query:
            self._query = query
            self.queryChanged.emit()
            self._refresh()

    query = Property(str, _get_query, _set_query, notify=queryChanged)

    @Slot(str)
    def use(self, emoji: str) -> None:
        """Remember as recently used. The open grid doesn't reorder under the cursor: the recent
        tab picks it up on the next refresh()."""
        self._catalog.use(emoji)

    @Slot()
    def refresh(self) -> None:
        self._refresh()

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self) -> dict[int, QByteArray]:
        return {Role.Emoji: QByteArray(b"emoji"), Role.Name: QByteArray(b"name")}

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        emoji, name = self._items[index.row()]
        if role in (Role.Emoji, Qt.ItemDataRole.DisplayRole):
            return emoji
        if role == Role.Name:
            return name
        return None

    def _refresh(self) -> None:
        self.beginResetModel()
        self._items = (self._catalog.search(self._query) if self._query.strip()
                       else self._catalog.items(self._category))
        self.endResetModel()
