"""History of one open chat, newest message first (row 0 = newest).

Newest-first order lets the QML view use BottomToTop layout: older pages are appended at the end
of the model (visually on top), so loading history doesn't shift what the user is looking at.

Mutations go through a HistoryListener so a Qt model can wrap them in begin/end calls:
the listener must call `commit()` exactly once between its begin and end notifications.

The loaded window may stop short of the newest message (`reached_end` False) after
`load_around()` (opening on the first unread message, jumping to an old one): newer pages then
come from `load_newer()`, and live messages past the window are not inserted (they would leave
a gap) until `load_latest()` brings the newest page back.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
from collections.abc import Callable
from typing import Any, Protocol

from ..td.client import Event, TdClient, TdError
from .forums import message_topic_id

log = logging.getLogger(__name__)

Message = dict[str, Any]
Commit = Callable[[], None]

DELETED_REPLY: Message = {"@type": "deletedMessage"}


class HistoryListener(Protocol):
    def history_insert(self, row: int, count: int, commit: Commit) -> None: ...
    def history_remove(self, row: int, count: int, commit: Commit) -> None: ...
    def history_changed(self, row: int) -> None: ...
    def history_reset(self, commit: Commit) -> None: ...


class _CommitOnly:
    def history_insert(self, row: int, count: int, commit: Commit) -> None:
        commit()

    def history_remove(self, row: int, count: int, commit: Commit) -> None:
        commit()

    def history_changed(self, row: int) -> None:
        pass

    def history_reset(self, commit: Commit) -> None:
        commit()


class ChatHistory:
    PAGE = 50

    def __init__(
        self, client: TdClient, chat_id: int, listener: HistoryListener | None = None,
        last_message_id: Callable[[], int] | None = None, topic_id: int = 0,
    ) -> None:
        """`last_message_id`: the chat's newest message (ChatStore), to know when a window
        loaded around an old message has reached it; without it, a short page means so.
        `topic_id`: only this forum topic (getForumTopicHistory, live messages filtered)."""
        self._client = client
        self.chat_id = chat_id
        self.topic_id = topic_id
        self._last_message_id = last_message_id or (lambda: 0)
        self.messages: list[Message] = []
        self._keys: list[int] = []  # -message_id, ascending == newest first
        self.reached_start = False
        self.reached_end = True  # the newest message is loaded (or nothing is yet)
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
            "updateMessageInteractionInfo": self._on_interaction_info,
            "updateMessageIsPinned": self._on_is_pinned,
            "updatePoll": self._on_poll,
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
        if self._mine({"chat_id": self.chat_id, **message}):
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

    async def load_around(self, message_id: int) -> bool:
        """Replace the window with messages around `message_id` (it and older, plus up to half
        a page newer). Returns whether the message is loaded."""
        while self.loading:  # a page requested by the view is in flight
            await asyncio.sleep(0.02)
        self.loading = True
        try:
            newer = self.PAGE // 2
            batch: list[Message] = []
            for _ in range(3):  # like load_initial: the first answer may be only the cache
                batch = await self._fetch(message_id, offset=-newer, keep_from=True)
                if self._disposed:
                    return False
                if len(batch) >= self.PAGE // 2 or any(m["id"] == message_id for m in batch):
                    break
            fresh = sorted({m["id"]: m for m in batch}.values(), key=lambda m: m["id"],
                           reverse=True)

            def commit() -> None:
                self.messages = fresh
                self._keys = [-m["id"] for m in fresh]
                self.reached_start = False
                self.reached_end = self._at_end(fresh, sum(m["id"] > message_id for m in fresh)
                                                < newer)

            self._listener.history_reset(commit)
            return self.row_of(message_id) >= 0
        finally:
            self.loading = False

    async def load_newer(self) -> None:
        """The next page towards the newest message, when the window stops short of it."""
        if self.loading or self.reached_end or not self.messages:
            return
        self.loading = True
        try:
            newest = self.messages[0]["id"]
            batch = await self._fetch(newest, offset=-self.PAGE, limit=self.PAGE + 1)
            if self._disposed:
                return
            fresh = sorted((m for m in batch if m["id"] > newest and self.row_of(m["id"]) < 0),
                           key=lambda m: m["id"], reverse=True)
            if self._at_end(fresh or self.messages[:1], len(fresh) < self.PAGE):
                self.reached_end = True
            if fresh:
                def commit() -> None:
                    self.messages[:0] = fresh
                    self._keys[:0] = [-m["id"] for m in fresh]

                self._listener.history_insert(0, len(fresh), commit)
                self._listener.history_changed(len(fresh))  # boundary row's grouping
        finally:
            self.loading = False

    def _at_end(self, newest_first: list[Message], short_page: bool) -> bool:
        last = self._last_message_id()
        if last and newest_first:
            return newest_first[0]["id"] >= last
        return short_page

    async def load_latest(self) -> None:
        """Back to the newest page (after load_around), e.g. before sending."""
        if self.reached_end:
            return
        while self.loading:
            await asyncio.sleep(0.02)

        def commit() -> None:
            self.messages, self._keys = [], []
            self.reached_start, self.reached_end = False, True

        self._listener.history_reset(commit)
        await self.load_initial()

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

    async def _fetch(self, from_id: int, offset: int = 0, limit: int = 0,
                     keep_from: bool = False) -> list[Message]:
        """A page of history from `from_id` (older; with a negative offset also newer)."""
        request: dict[str, Any] = {
            "@type": "getChatHistory", "chat_id": self.chat_id, "from_message_id": from_id,
            "offset": offset, "limit": limit or self.PAGE, "only_local": False}
        if self.topic_id:
            request = {"@type": "getForumTopicHistory", "chat_id": self.chat_id,
                       "forum_topic_id": self.topic_id, "from_message_id": from_id,
                       "offset": offset, "limit": limit or self.PAGE}
        try:
            result = await self._client.send(request)
        except TdError as e:
            log.warning("getChatHistory(%s, %s) failed: %s", self.chat_id, from_id, e)
            return []
        return [m for m in result.get("messages") or []
                if m and (keep_from or m["id"] != from_id)]

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

    def _past_window(self, message: Message) -> bool:
        """Newer than the loaded window that stops short of the newest message."""
        return not self.reached_end and bool(self.messages) and (
            message["id"] > self.messages[0]["id"])

    def _mine(self, message: Message) -> bool:
        return message.get("chat_id") == self.chat_id and (
            not self.topic_id or message_topic_id(message) == self.topic_id)

    def _on_new_message(self, event: Event) -> None:
        message = event["message"]
        if self._mine(message) and not self._past_window(message):
            self._insert(message)

    def _on_send_result(self, event: Event) -> None:
        message = event["message"]
        if not self._mine(message):
            return
        self._remove(event["old_message_id"])
        if not self._past_window(message):
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

    def _on_interaction_info(self, event: Event) -> None:  # reactions, views, replies
        if event.get("chat_id") != self.chat_id:
            return
        row = self.row_of(event["message_id"])
        if row >= 0:
            self.messages[row] = {**self.messages[row],
                                  "interaction_info": event.get("interaction_info")}
            self._listener.history_changed(row)

    def _on_poll(self, event: Event) -> None:
        """Votes changed: the poll is sent by its id, without the message."""
        poll = event["poll"]
        for row, message in enumerate(self.messages):
            content = message.get("content") or {}
            if (content.get("poll") or {}).get("id") == poll.get("id"):
                self.messages[row] = {**message, "content": {**content, "poll": poll}}
                self._listener.history_changed(row)

    def _on_is_pinned(self, event: Event) -> None:
        if event.get("chat_id") != self.chat_id:
            return
        row = self.row_of(event["message_id"])
        if row >= 0:
            self.messages[row] = {**self.messages[row], "is_pinned": bool(event["is_pinned"])}
            self._listener.history_changed(row)

    def _on_delete(self, event: Event) -> None:
        if event.get("chat_id") != self.chat_id or event.get("from_cache"):
            return  # from_cache: only evicted from TDLib's cache, not actually deleted
        for message_id in event.get("message_ids", []):
            self._remove(message_id)
