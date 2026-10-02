"""Qt model of search results: matching chats, then matching messages (sections as rows)."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from enum import IntEnum, auto
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    QTimer,
    Signal,
    Slot,
)

from ..services.search import Hit, SearchService
from ..store.chats import ChatStore
from ..store.format import initials, short_time

log = logging.getLogger(__name__)

AVATAR_COLORS = 7
MAX_CHATS = 5
SNIPPET_CHARS = 140
AnyIndex = QModelIndex | QPersistentModelIndex
_WORD = re.compile(r"\w+", re.UNICODE)


class Role(IntEnum):
    Kind = Qt.ItemDataRole.UserRole + 1  # section | chat | message
    ChatId = auto()
    MessageId = auto()
    Title = auto()
    Subtitle = auto()
    Snippet = auto()
    Time = auto()
    AvatarSource = auto()
    Initials = auto()
    ColorIndex = auto()
    ByMeaning = auto()


def snippet_html(text: str, query: str, color: str, limit: int = SNIPPET_CHARS) -> str:
    """A window of `text` around the first query word, query words (as prefixes) in bold.
    Output is Qt StyledText (`<font color>`, not CSS)."""
    words = [w for w in _WORD.findall(query.lower()) if len(w) > 1]
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\w*",
                         re.IGNORECASE | re.UNICODE) if words else None
    text = " ".join(text.split())
    start = 0
    if pattern and (first := pattern.search(text)) and first.start() > limit // 3:
        start = text.rfind(" ", 0, first.start() - limit // 4) + 1
    window = text[start:start + limit]
    prefix = "…" if start else ""
    suffix = "…" if start + limit < len(text) else ""
    if pattern is None:
        return prefix + html.escape(window) + suffix
    out: list[str] = []
    position = 0
    for match in pattern.finditer(window):
        out.append(html.escape(window[position:match.start()]))
        out.append(f'<b><font color="{color}">{html.escape(match.group(0))}</font></b>')
        position = match.end()
    out.append(html.escape(window[position:]))
    return prefix + "".join(out) + suffix


class SearchModel(QAbstractListModel):
    queryChanged = Signal()
    busyChanged = Signal()
    statusChanged = Signal()

    def __init__(self, service: SearchService, chats: ChatStore, parent: Any = None) -> None:
        super().__init__(parent)
        self._service = service
        self._chats = chats
        self._query = ""
        self._rows: list[dict[str, Any]] = []
        self._busy = False
        self._generation = 0
        self._accent = "#0E7C66"
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._run)
        service.subscribe(self.statusChanged.emit)

    # --- properties -------------------------------------------------------------------------

    def _get_query(self) -> str:
        return self._query

    def _set_query(self, query: str) -> None:
        if query == self._query:
            return
        self._query = query
        self.queryChanged.emit()
        if query.strip():
            self._timer.start()
        else:
            self._timer.stop()
            self._generation += 1
            self._set_rows([])
            self._set_busy(False)

    query = Property(str, _get_query, _set_query, notify=queryChanged)

    def _get_accent(self) -> str:
        return self._accent

    def _set_accent(self, value: str) -> None:
        self._accent = value

    accentColor = Property(str, _get_accent, _set_accent)

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=statusChanged)
    def semanticSupported(self) -> bool:
        return self._service.semantic_supported

    @Property(bool, notify=statusChanged)
    def semanticEnabled(self) -> bool:
        return self._service.semantic_enabled

    @Property(str, notify=statusChanged)
    def embeddingModel(self) -> str:
        return self._service.embedding_model

    @Property(str, notify=statusChanged)
    def modelState(self) -> str:
        return self._service.status().model_state

    @Property(str, notify=statusChanged)
    def modelError(self) -> str:
        return self._service.status().model_error

    @Property(int, notify=statusChanged)
    def indexedMessages(self) -> int:
        return self._service.status().docs

    @Property(int, notify=statusChanged)
    def indexedChats(self) -> int:
        return self._service.status().chats

    @Property(int, notify=statusChanged)
    def embeddedMessages(self) -> int:
        return self._service.status().vectors

    @Property(bool, notify=statusChanged)
    def indexing(self) -> bool:
        return self._service.status().indexing

    @Slot(bool)
    def setSemanticEnabled(self, enabled: bool) -> None:
        self._service.set_semantic(enabled)
        if self._query.strip():
            self._timer.start()

    @Slot()
    def clear(self) -> None:
        self._set_query("")

    # --- QAbstractListModel -----------------------------------------------------------------

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, QByteArray]:
        return {role.value: QByteArray((role.name[0].lower() + role.name[1:]).encode())
                for role in Role}

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Role.AvatarSource:
            chat = self._chats.chats.get(row["chatId"])
            if chat is None or row["kind"] == "section":
                return ""
            if chat.photo_path and chat.photo_file_id is not None:
                return self._chats.files.url("avatar", chat.photo_file_id)
            self._chats.request_photo(chat.id)
            return ""
        name = role_name(role)
        return row.get(name, "") if name else None

    # --- internals --------------------------------------------------------------------------

    def _run(self) -> None:
        self._generation += 1
        generation = self._generation
        query = self._query.strip()
        chats = self._chat_rows(query)
        self._set_rows(chats + self._rows_after_chats())
        self._set_busy(True)

        async def search() -> None:
            try:
                hits = await self._service.search(query)
            except Exception:
                log.exception("Search failed")
                hits = []
            if generation != self._generation:
                return  # the query changed meanwhile
            self._set_rows(chats + self._message_rows(hits, query))
            self._set_busy(False)

        task = asyncio.ensure_future(search())
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    def _rows_after_chats(self) -> list[dict[str, Any]]:
        """While a new query runs, keep showing the previous message results."""
        start = next((i for i, r in enumerate(self._rows)
                      if r["kind"] == "section" and r["title"] == "messages"), None)
        return self._rows[start:] if start is not None else []

    def _chat_rows(self, query: str) -> list[dict[str, Any]]:
        needle = query.lower()
        matches = [c for c in self._chats.chats.values()
                   if needle in c.title.lower() and c.positions]
        matches.sort(key=lambda c: (not c.title.lower().startswith(needle),
                                    -max(p.order for p in c.positions.values())))
        rows = [self._section("chats")] if matches else []
        for chat in matches[:MAX_CHATS]:
            rows.append({"kind": "chat", "chatId": chat.id, "messageId": 0, "title": chat.title,
                         "subtitle": "", "snippet": "", "time": "",
                         "initials": initials(chat.title),
                         "colorIndex": abs(chat.id) % AVATAR_COLORS, "byMeaning": False})
        return rows

    def _message_rows(self, hits: list[Hit], query: str) -> list[dict[str, Any]]:
        if not hits:
            return []
        rows = [self._section("messages")]
        for hit in hits:
            chat = self._chats.chats.get(hit.chat_id)
            title = chat.title if chat else ""
            rows.append({
                "kind": "message", "chatId": hit.chat_id, "messageId": hit.message_id,
                "title": title, "subtitle": hit.sender if hit.sender != title else "",
                "snippet": snippet_html(hit.text, query, self._accent),
                "time": short_time(hit.date) if hit.date else "",
                "initials": initials(title), "colorIndex": abs(hit.chat_id) % AVATAR_COLORS,
                "byMeaning": hit.semantic and not hit.keyword,
            })
        return rows

    @staticmethod
    def _section(title: str) -> dict[str, Any]:
        """Section header row; the title is a key ("chats" | "messages") translated in QML."""
        return {"kind": "section", "chatId": 0, "messageId": 0, "title": title, "subtitle": "",
                "snippet": "", "time": "", "initials": "", "colorIndex": 0, "byMeaning": False}

    def _set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit()


def role_name(role: int) -> str:
    try:
        name = Role(role).name
    except ValueError:
        return ""
    return name[0].lower() + name[1:]
