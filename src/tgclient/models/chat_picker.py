"""Chats to pick from (forwarding): the main list in Telegram order, filtered by title."""

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

from ..store.chats import MAIN, Chat, ChatStore
from ..store.format import initials
from ..store.users import UserStore

AVATAR_COLORS = 7
AnyIndex = QModelIndex | QPersistentModelIndex


class Role(IntEnum):
    ChatId = Qt.ItemDataRole.UserRole + 1
    Title = auto()
    AvatarSource = auto()
    Initials = auto()
    ColorIndex = auto()


class ChatPickerModel(QAbstractListModel):
    """A snapshot, rebuilt by refresh() and on filter changes; the picker is short-lived."""

    filterChanged = Signal()

    def __init__(self, store: ChatStore, users: UserStore, parent: Any = None) -> None:
        super().__init__(parent)
        self._store = store
        self._users = users
        self._filter = ""
        self._rows: list[Chat] = []

    def _get_filter(self) -> str:
        return self._filter

    def _set_filter(self, value: str) -> None:
        if value != self._filter:
            self._filter = value
            self.filterChanged.emit()
            self.refresh()

    filter = Property(str, _get_filter, _set_filter, notify=filterChanged)

    @Slot()
    def refresh(self) -> None:
        needle = self._filter.strip().casefold()
        self.beginResetModel()
        self._rows = [
            chat for chat in self._store.chats_in(MAIN)
            if chat.type != "channel"  # TODO: channels where we may post
            and (not needle or needle in self._title(chat).casefold())
        ]
        self.endResetModel()

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, QByteArray]:
        return {role.value: QByteArray((role.name[0].lower() + role.name[1:]).encode())
                for role in Role}

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        chat = self._rows[index.row()]
        match role:
            case Role.ChatId:
                return chat.id
            case Role.Title | Qt.ItemDataRole.DisplayRole:
                return self._title(chat)
            case Role.AvatarSource:
                if chat.photo_path and chat.photo_file_id is not None:
                    return self._store.files.url("avatar", chat.photo_file_id)
                self._store.request_photo(chat.id)
                return ""
            case Role.Initials:
                return initials(chat.title)
            case Role.ColorIndex:
                return abs(chat.id) % AVATAR_COLORS
        return None

    def _title(self, chat: Chat) -> str:
        if chat.type == "private" and chat.peer_id and chat.peer_id == self._users.my_id:
            return "Saved Messages"
        return chat.title
