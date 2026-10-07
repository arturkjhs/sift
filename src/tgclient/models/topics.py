"""Qt model of the open forum's topics (pinned first, then by the latest message)."""

from __future__ import annotations

from enum import IntEnum, auto
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from ..store.chats import Chat, ChatStore
from ..store.forums import ForumStore, Topic
from ..store.format import message_preview, short_time
from ..store.users import UserStore
from .messages import MessageListModel

AnyIndex = QModelIndex | QPersistentModelIndex


class Role(IntEnum):
    TopicId = Qt.ItemDataRole.UserRole + 1
    Name = auto()
    IconColor = auto()  # "#rrggbb"
    Preview = auto()
    Time = auto()
    UnreadCount = auto()
    MentionCount = auto()
    Pinned = auto()
    Closed = auto()
    General = auto()


class TopicListModel(QAbstractListModel):
    def __init__(self, forums: ForumStore, chats: ChatStore, users: UserStore,
                 messages: MessageListModel, parent: Any = None) -> None:
        super().__init__(parent)
        self._forums = forums
        self._chats = chats
        self._users = users
        self._messages = messages
        self._chat_id = 0
        self._rows: list[Topic] = []
        forums.subscribe(self._on_forums)
        messages.chatChanged.connect(self._on_chat_changed)

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, QByteArray]:
        return {role.value: QByteArray((role.name[0].lower() + role.name[1:]).encode())
                for role in Role}

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        topic = self._rows[index.row()]
        match role:
            case Role.TopicId:
                return topic.id
            case Role.Name:
                return topic.name or ("General" if topic.is_general else "")
            case Role.IconColor:
                return f"#{topic.icon_color & 0xFFFFFF:06x}"
            case Role.Preview:
                chat = self._chats.chats.get(topic.chat_id)
                if chat is None or not topic.last_message:
                    return ""
                shown = Chat(id=chat.id, title=chat.title, type=chat.type,
                             last_message=topic.last_message)
                return message_preview(shown, self._users)
            case Role.Time:
                date = (topic.last_message or {}).get("date", 0)
                return short_time(date) if date else ""
            case Role.UnreadCount:
                return topic.unread_count
            case Role.MentionCount:
                return topic.unread_mention_count
            case Role.Pinned:
                return topic.is_pinned
            case Role.Closed:
                return topic.is_closed
            case Role.General:
                return topic.is_general
        return None

    def _on_chat_changed(self) -> None:
        chat_id = int(self._messages.chatId or 0) if self._messages.isForum else 0
        if chat_id != self._chat_id:
            self._chat_id = chat_id
            self._reload()

    def _on_forums(self, kind: str, payload: Any) -> None:
        if not self._chat_id:
            return
        if (kind == "topics" and payload == self._chat_id) or (
                kind == "topic" and payload[0] == self._chat_id):
            self._reload()

    def _reload(self) -> None:
        fresh = self._forums.sorted_topics(self._chat_id) if self._chat_id else []
        if [t.id for t in fresh] == [t.id for t in self._rows]:
            self._rows = fresh
            if fresh:
                self.dataChanged.emit(self.index(0), self.index(len(fresh) - 1))
            return
        self.beginResetModel()
        self._rows = fresh
        self.endResetModel()
