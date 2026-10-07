"""Qt model of one sender's messages ("Messages from <name>"): in the open chat, newest first,
paged as the list scrolls; with a query, only matches (highlighted). Exposed to QML as
`personMessages`. Rows: {kind: "message", chatId, messageId, chatTitle, topic, time, text}."""

from __future__ import annotations

import asyncio
import html
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

from ..services.person_search import PersonSearch
from ..services.summary import sender_object
from ..store.chats import ChatStore
from ..store.forums import ForumStore, message_topic_id
from ..store.format import content_preview, media_label, message_body, message_stamp
from ..store.richtext import highlight_html
from ..store.users import UserStore

log = logging.getLogger(__name__)

AnyIndex = QModelIndex | QPersistentModelIndex
SNIPPET_CHARS = 400
QUERY_DELAY = 0.3  # seconds of no typing before searching


class Role(IntEnum):
    Kind = Qt.ItemDataRole.UserRole + 1  # message | header
    ChatId = auto()
    MessageId = auto()
    ChatTitle = auto()
    Topic = auto()
    Time = auto()
    Text = auto()  # rich text: the message (or a window of it) with the query highlighted
    Media = auto()  # "Photo", "Voice message"… for non-text messages
    More = auto()  # header rows: this chat has more results


class PersonMessagesModel(QAbstractListModel):
    changed = Signal()

    def __init__(self, search: PersonSearch, chats: ChatStore, users: UserStore,
                 forums: ForumStore | None = None, parent: Any = None) -> None:
        super().__init__(parent)
        self._search = search
        self._chats = chats
        self._users = users
        self._forums = forums
        self._chat_id = 0
        self._sender = ""  # "user:<id>" | "chat:<id>"
        self._name = ""
        self._query = ""
        self._rows: list[dict[str, Any]] = []
        self._next_from = 0
        self._busy = False
        self._generation = 0
        self._color = "#553FB295"
        self._tasks: set[asyncio.Task[Any]] = set()
        self._typing: asyncio.Task[Any] | None = None

    # --- QML API ----------------------------------------------------------------------------

    @Slot("QVariant", str, str)
    def open(self, chat_id: Any, sender: str, name: str) -> None:
        """The sender's messages in this chat (a private chat: everything they wrote)."""
        if sender_object(sender) is None:
            return
        self._chat_id, self._sender, self._name = int(chat_id or 0), sender, name
        self._query = ""
        self._restart()

    @Property(str, notify=changed)
    def sender(self) -> str:
        return self._sender

    @Property(str, notify=changed)
    def senderName(self) -> str:
        return self._name

    @Property(bool, notify=changed)
    def onBehalf(self) -> bool:
        """A chat as the sender (anonymous admins, channels): shown as "on behalf of"."""
        return self._sender.startswith("chat:")

    @Property("QVariant", notify=changed)
    def chatId(self) -> int:
        return self._chat_id

    def _get_query(self) -> str:
        return self._query

    def _set_query(self, query: str) -> None:
        if query != self._query:
            self._query = query
            self.changed.emit()
            if self._typing is not None:
                self._typing.cancel()
            self._typing = asyncio.ensure_future(self._restart_later())

    query = Property(str, _get_query, _set_query, notify=changed)

    def _get_color(self) -> str:
        return self._color

    def _set_color(self, value: str) -> None:
        self._color = value

    highlightColor = Property(str, _get_color, _set_color)

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=changed)
    def more(self) -> bool:
        return bool(self._next_from)

    @Property(int, notify=changed)
    def count(self) -> int:
        return sum(1 for r in self._rows if r["kind"] == "message")

    @Slot()
    def loadMore(self) -> None:
        if self._next_from and not self._busy and self._sender:
            self._spawn(self._load(self._generation, self._next_from))

    # --- QAbstractListModel -----------------------------------------------------------------

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, QByteArray]:
        return {role.value: QByteArray((role.name[0].lower() + role.name[1:]).encode())
                for role in Role}

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        if index.row() >= len(self._rows) - 5:
            self.loadMore()  # near the end: the next page
        try:
            name = Role(role).name
        except ValueError:
            return None
        return self._rows[index.row()].get(name[0].lower() + name[1:], "")

    # --- internals --------------------------------------------------------------------------

    async def _restart_later(self) -> None:
        await asyncio.sleep(QUERY_DELAY)
        self._restart()

    def _restart(self) -> None:
        self._generation += 1
        for task in list(self._tasks):
            task.cancel()
        self.beginResetModel()
        self._rows, self._next_from = [], 0
        self.endResetModel()
        self.changed.emit()
        if self._sender and self._chat_id:
            self._spawn(self._load(self._generation, 0))

    async def _load(self, generation: int, from_id: int) -> None:
        self._set_busy(True)
        try:
            messages, next_from = await self._search.page(
                self._chat_id, self._sender, self._query.strip(), from_id)
        finally:
            if generation == self._generation:
                self._set_busy(False)
        if generation != self._generation:
            return
        known = {(r["chatId"], r["messageId"]) for r in self._rows}
        fresh = [self.row(m) for m in messages if (m["chat_id"], m["id"]) not in known]
        if fresh:
            self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows) + len(fresh) - 1)
            self._rows.extend(fresh)
            self.endInsertRows()
        self._next_from = next_from
        self.changed.emit()

    def row(self, message: dict[str, Any]) -> dict[str, Any]:
        """One result row (also for the common-chats results)."""
        chat = self._chats.chats.get(message.get("chat_id", 0))
        content = message.get("content", {})
        body = (message_body(content) or {}).get("text", "") or ""
        media = media_label(content) if content.get("@type") != "messageText" else ""
        text = body or ("" if media else content_preview(content))
        if len(text) > SNIPPET_CHARS:
            text = text[:SNIPPET_CHARS].rstrip() + "…"
        rich = html.escape(text).replace("\n", "<br>")
        if self._query.strip():
            rich = highlight_html(rich, self._query, self._color)
        topic = ""
        if self._forums is not None and chat is not None and chat.type == "supergroup" \
                and self._forums.is_forum(chat.peer_id):
            found = self._forums.get(chat.id, message_topic_id(message))
            topic = found.name if found else ""
        return {"kind": "message", "chatId": message.get("chat_id", 0),
                "messageId": message["id"], "chatTitle": chat.title if chat else "",
                "topic": topic, "time": message_stamp(message.get("date", 0)),
                "text": rich, "media": media, "more": False}

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.changed.emit()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("Person search failed", exc_info=task.exception())
