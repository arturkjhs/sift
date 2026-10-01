"""Qt model of folder tabs: the user's chat folders (with "All chats" in place) plus Archive."""

from __future__ import annotations

from enum import IntEnum, auto
from typing import Any

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QPersistentModelIndex, Qt

from ..store.chats import ARCHIVE, ChatStore, Folder

AnyIndex = QModelIndex | QPersistentModelIndex


class Role(IntEnum):
    Key = Qt.ItemDataRole.UserRole + 1
    Title = auto()
    UnreadCount = auto()


class FolderModel(QAbstractListModel):
    def __init__(self, store: ChatStore, parent: Any = None) -> None:
        super().__init__(parent)
        self._store = store
        self._items = self._build()
        store.subscribe(self._on_store_change)

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self) -> dict[int, QByteArray]:
        return {Role.Key: QByteArray(b"key"), Role.Title: QByteArray(b"title"),
                Role.UnreadCount: QByteArray(b"unreadCount")}

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        match role:
            case Role.Key:
                return item.key
            case Role.Title | Qt.ItemDataRole.DisplayRole:
                return item.title
            case Role.UnreadCount:
                return self._store.unread.get(item.key, 0)
        return None

    def _build(self) -> list[Folder]:
        return [*self._store.folders, Folder(key=ARCHIVE, title="Archive")]

    def _on_store_change(self, kind: str, payload: Any) -> None:
        if kind == "folders":
            self.beginResetModel()
            self._items = self._build()
            self.endResetModel()
        elif kind == "unread":
            for row, item in enumerate(self._items):
                if item.key == payload:
                    index = self.index(row)
                    self.dataChanged.emit(index, index, [Role.UnreadCount])
