"""Qt model of one chat list (main, archive or a folder), ordered like Telegram orders it.

Rows are kept sorted by (-order, -chat_id). On every chat change the row is inserted, removed,
moved (beginMoveRows, so QML ListView can animate) or just refreshed.
"""

from __future__ import annotations

import asyncio
import bisect
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

from ..store.chats import MAIN, ChatStore
from ..store.format import initials, message_preview, message_time
from ..store.users import UserStore

AVATAR_COLORS = 7
Key = tuple[int, int]
AnyIndex = QModelIndex | QPersistentModelIndex


class Role(IntEnum):
    ChatId = Qt.ItemDataRole.UserRole + 1
    Title = auto()
    Preview = auto()
    Time = auto()
    UnreadCount = auto()
    MentionCount = auto()
    Muted = auto()
    Pinned = auto()
    AvatarSource = auto()
    Initials = auto()
    ColorIndex = auto()
    ChatType = auto()


class ChatListModel(QAbstractListModel):
    listKeyChanged = Signal()
    fullyLoadedChanged = Signal()

    def __init__(self, store: ChatStore, users: UserStore, parent: Any = None) -> None:
        super().__init__(parent)
        self._store = store
        self._users = users
        self._list_key = MAIN
        self._rows: list[Key] = []
        self._keys: dict[int, Key] = {}
        store.subscribe(self._on_store_change)

    # --- QML API ----------------------------------------------------------------------------

    @Property(str, notify=listKeyChanged)
    def listKey(self) -> str:
        return self._list_key

    @Property(bool, notify=fullyLoadedChanged)
    def fullyLoaded(self) -> bool:
        return self._store.is_fully_loaded(self._list_key)

    @Slot(str)
    def setList(self, key: str) -> None:
        if key != self._list_key:
            self._list_key = key
            self.listKeyChanged.emit()
        self._rebuild()
        self.loadMore()

    @Slot()
    def loadMore(self) -> None:
        if not self._store.is_fully_loaded(self._list_key):
            asyncio.ensure_future(self._load(self._list_key))

    # --- QAbstractListModel -----------------------------------------------------------------

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, QByteArray]:
        return {role.value: QByteArray(_role_name(role).encode()) for role in Role}

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        chat = self._store.chats.get(-self._rows[index.row()][1])
        if chat is None:
            return None

        match role:
            case Role.ChatId:
                return chat.id
            case Role.Title | Qt.ItemDataRole.DisplayRole:
                return chat.title
            case Role.Preview:
                return message_preview(chat, self._users)
            case Role.Time:
                return message_time(chat)
            case Role.UnreadCount:
                return chat.unread_count
            case Role.MentionCount:
                return chat.unread_mention_count
            case Role.Muted:
                return self._store.is_muted(chat)
            case Role.Pinned:
                position = chat.positions.get(self._list_key)
                return bool(position and position.is_pinned)
            case Role.AvatarSource:
                if chat.photo_path and chat.photo_file_id is not None:
                    return f"image://tg/avatar/{chat.photo_file_id}"
                self._store.request_photo(chat.id)  # lazy: only for rows QML actually shows
                return ""
            case Role.Initials:
                return initials(chat.title)
            case Role.ColorIndex:
                return abs(chat.id) % AVATAR_COLORS
            case Role.ChatType:
                return chat.type
        return None

    # --- internals --------------------------------------------------------------------------

    async def _load(self, key: str) -> None:
        await self._store.load_more(key)
        if key == self._list_key:
            self.fullyLoadedChanged.emit()

    def _key_for(self, chat_id: int) -> Key | None:
        chat = self._store.chats.get(chat_id)
        position = chat.positions.get(self._list_key) if chat else None
        return (-position.order, -chat_id) if position else None

    def _rebuild(self) -> None:
        self.beginResetModel()
        self._keys = {}
        for chat_id in self._store.chats:
            key = self._key_for(chat_id)
            if key is not None:
                self._keys[chat_id] = key
        self._rows = sorted(self._keys.values())
        self.endResetModel()
        self.fullyLoadedChanged.emit()

    def _on_store_change(self, kind: str, payload: Any) -> None:
        if kind == "chat":
            self._sync_chat(payload)

    def _sync_chat(self, chat_id: int) -> None:
        new_key = self._key_for(chat_id)
        old_key = self._keys.get(chat_id)
        root = QModelIndex()

        if old_key is None and new_key is None:
            return

        if old_key is None:
            assert new_key is not None
            row = bisect.bisect_left(self._rows, new_key)
            self.beginInsertRows(root, row, row)
            self._rows.insert(row, new_key)
            self._keys[chat_id] = new_key
            self.endInsertRows()
            return

        old_row = bisect.bisect_left(self._rows, old_key)

        if new_key is None:
            self.beginRemoveRows(root, old_row, old_row)
            del self._rows[old_row]
            del self._keys[chat_id]
            self.endRemoveRows()
            return

        row = old_row
        if new_key != old_key:
            insert_at = bisect.bisect_left(self._rows, new_key)
            new_row = insert_at - 1 if insert_at > old_row else insert_at
            if new_row != old_row:
                destination = new_row + 1 if new_row > old_row else new_row
                self.beginMoveRows(root, old_row, old_row, root, destination)
                del self._rows[old_row]
                self._rows.insert(new_row, new_key)
                self._keys[chat_id] = new_key
                self.endMoveRows()
                row = new_row
            else:
                self._rows[old_row] = new_key
                self._keys[chat_id] = new_key

        index = self.index(row)
        self.dataChanged.emit(index, index)


def _role_name(role: Role) -> str:
    name = role.name
    return name[0].lower() + name[1:]
