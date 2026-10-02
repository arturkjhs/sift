"""History of one open chat, newest message first (row 0 = newest).

Newest-first order lets the QML view use BottomToTop layout: older pages are appended at the end
of the model (visually on top), so loading history doesn't shift what the user is looking at.

Mutations go through a HistoryListener so a Qt model can wrap them in begin/end calls:
the listener must call `commit()` exactly once between its begin and end notifications.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
from collections.abc import Callable
from typing import Any, Protocol

from ..td.client import Event, TdClient, TdError

log = logging.getLogger(__name__)

Message = dict[str, Any]
Commit = Callable[[], None]

DELETED_REPLY: Message = {"@type": "deletedMessage"}


class HistoryListener(Protocol):
    def history_insert(self, row: int, count: int, commit: Commit) -> None: ...
    def history_remove(self, row: int, count: int, commit: Commit) -> None: ...
    def history_changed(self, row: int) -> None: ...


class _CommitOnly:
    def history_insert(self, row: int, count: int, commit: Commit) -> None:
        commit()

    def history_remove(self, row: int, count: int, commit: Commit) -> None:
        commit()

    def history_changed(self, row: int) -> None:
        pass


class ChatHistory:
    PAGE = 50

    def __init__(
        self, client: TdClient, chat_id: int, listener: HistoryListener | None = None
    ) -> None:
        self._client = client
        self.chat_id = chat_id
        self.messages: list[Message] = []
        self._keys: list[int] = []  # -message_id, ascending == newest first
        self.reached_start = False
        self.loading = False
        self._listener: HistoryListener = listener or _CommitOnly()
        self._replies: dict[int, Message] = {}  # replied-to message id -> message
        self._reply_requests: set[int] = set()  # ids of messages whose reply we fetched
        self._disposed = False
        handlers: dict[str, Callable[[Event], None]] = {
            "updateNewMessage": self._on_new_message,
            "updateMessageSendSucceeded": self._on_send_result,
            "updateMessageSendFailed": self._on_send_result,
            "updateMessageContent": self._on_content,
            "updateMessageEdited": self._on_edited,
            "updateDeleteMessages": self._on_delete,
        }
        self._unsubscribe = [client.on(t, h) for t, h in handlers.items()]

    def dispose(self) -> None:
        self._disposed = True
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []

    # --- queries ----------------------------------------------------------------------------

    def row_of(self, message_id: int) -> int:
        row = bisect.bisect_left(self._keys, -message_id)
        return row if row < len(self._keys) and self._keys[row] == -message_id else -1

    def get(self, message_id: int) -> Message | None:
        row = self.row_of(message_id)
        return self.messages[row] if row >= 0 else None

    def reply_target_id(self, message: Message) -> int:
        reply_to = message.get("reply_to")
        if isinstance(reply_to, dict) and reply_to.get("@type") == "messageReplyToMessage":
            if reply_to.get("chat_id", self.chat_id) in (self.chat_id, 0):
                return int(reply_to.get("message_id") or 0)
            return 0
        return int(message.get("reply_to_message_id") or 0)  # older TDLib

    def reply_message(self, message: Message) -> Message | None:
        """The replied-to message if known; otherwise starts fetching it and returns None."""
        target = self.reply_target_id(message)
        if not target:
            return None
        known = self.get(target) or self._replies.get(target)
        if known is not None:
            return known
        if message["id"] not in self._reply_requests:
            self._reply_requests.add(message["id"])
            asyncio.ensure_future(self._fetch_reply(message["id"], target))
        return None

    def add(self, message: Message) -> None:
        """Insert or replace a message (e.g. the result of sendMessage)."""
        if message.get("chat_id", self.chat_id) == self.chat_id:
            self._insert(message)

    # --- loading ----------------------------------------------------------------------------

    async def load_initial(self) -> None:
        """Load the newest page. TDLib often answers the first call with only the last message
        from its local cache, so keep asking until a page is filled or history ends."""
        if self.loading:
            return
        self.loading = True
        try:
            from_id = 0
            for _ in range(5):
                batch = await self._fetch(from_id)
                if self._disposed:
                    return
                if not batch:
                    self._set_reached_start()
                    break
                self._insert_many(batch)
                from_id = self.messages[-1]["id"]
                if len(self.messages) >= self.PAGE:
                    break
        finally:
            self.loading = False

    async def load_older(self) -> None:
        if self.loading or self.reached_start or not self.messages:
            return
        self.loading = True
        try:
            batch = await self._fetch(self.messages[-1]["id"])
            if self._disposed:
                return
            if batch:
                self._insert_many(batch)
            else:
                self._set_reached_start()
        finally:
            self.loading = False

    async def load_until(self, message_id: int, max_pages: int = 40) -> bool:
        """Page older history until `message_id` is loaded. Returns whether it is."""
        pages = 0
        while self.row_of(message_id) < 0:
            if self._disposed or self.reached_start or pages >= max_pages or (
                    self.messages and self.messages[-1]["id"] < message_id):
                return False
            if self.loading:  # a page requested by the view is in flight
                await asyncio.sleep(0.02)
                continue
            pages += 1
            await (self.load_older() if self.messages else self.load_initial())
        return True

    async def _fetch(self, from_id: int) -> list[Message]:
        try:
            result = await self._client.send({
                "@type": "getChatHistory", "chat_id": self.chat_id,
                "from_message_id": from_id, "offset": 0, "limit": self.PAGE, "only_local": False,
            })
        except TdError as e:
            log.warning("getChatHistory(%s, %s) failed: %s", self.chat_id, from_id, e)
            return []
        return [m for m in result.get("messages") or [] if m and m["id"] != from_id]

    async def _fetch_reply(self, message_id: int, target_id: int) -> None:
        try:
            reply = await self._client.send(
                {"@type": "getRepliedMessage", "chat_id": self.chat_id, "message_id": message_id}
            )
        except TdError:
            reply = DELETED_REPLY
        if self._disposed:
            return
        self._replies[target_id] = reply
        row = self.row_of(message_id)
        if row >= 0:
            self._listener.history_changed(row)

    # --- mutations --------------------------------------------------------------------------

    def _set_reached_start(self) -> None:
        if not self.reached_start:
            self.reached_start = True
            if self.messages:
                self._listener.history_changed(len(self.messages) - 1)  # day label may appear

    def _insert_many(self, batch: list[Message]) -> None:
        fresh = sorted(
            (m for m in batch if self.row_of(m["id"]) < 0), key=lambda m: m["id"], reverse=True
        )
        if fresh and self.messages and fresh[0]["id"] < self.messages[-1]["id"]:
            row = len(self.messages)  # a page of strictly older messages: one block at the end

            def commit() -> None:
                self.messages.extend(fresh)
                self._keys.extend(-m["id"] for m in fresh)

            self._listener.history_insert(row, len(fresh), commit)
            self._listener.history_changed(row - 1)  # boundary row's grouping/day label
            return
        for message in fresh:
            self._insert(message)

    def _insert(self, message: Message) -> None:
        key = -message["id"]
        row = bisect.bisect_left(self._keys, key)
        if row < len(self._keys) and self._keys[row] == key:
            self.messages[row] = message
            self._listener.history_changed(row)
            return

        def commit() -> None:
            self.messages.insert(row, message)
            self._keys.insert(row, key)

        self._listener.history_insert(row, 1, commit)
        self._changed_neighbors(row, inserted=True)

    def _remove(self, message_id: int) -> None:
        row = self.row_of(message_id)
        if row < 0:
            return

        def commit() -> None:
            del self.messages[row]
            del self._keys[row]

        self._listener.history_remove(row, 1, commit)
        self._changed_neighbors(row, inserted=False)

    def _changed_neighbors(self, row: int, inserted: bool) -> None:
        # Grouping and day labels depend on adjacent messages.
        candidates = (row - 1, row + 1) if inserted else (row - 1, row)
        for neighbor in candidates:
            if 0 <= neighbor < len(self.messages):
                self._listener.history_changed(neighbor)

    # --- update handlers --------------------------------------------------------------------

    def _on_new_message(self, event: Event) -> None:
        message = event["message"]
        if message.get("chat_id") == self.chat_id:
            self._insert(message)

    def _on_send_result(self, event: Event) -> None:
        message = event["message"]
        if message.get("chat_id") != self.chat_id:
            return
        self._remove(event["old_message_id"])
        self._insert(message)

    def _on_content(self, event: Event) -> None:
        if event.get("chat_id") != self.chat_id:
            return
        row = self.row_of(event["message_id"])
        if row >= 0:
            self.messages[row] = {**self.messages[row], "content": event["new_content"]}
            self._listener.history_changed(row)

    def _on_edited(self, event: Event) -> None:
        if event.get("chat_id") != self.chat_id:
            return
        row = self.row_of(event["message_id"])
        if row >= 0:
            self.messages[row] = {**self.messages[row], "edit_date": event.get("edit_date", 0)}
            self._listener.history_changed(row)

    def _on_delete(self, event: Event) -> None:
        if event.get("chat_id") != self.chat_id or event.get("from_cache"):
            return  # from_cache: only evicted from TDLib's cache, not actually deleted
        for message_id in event.get("message_ids", []):
            self._remove(message_id)
