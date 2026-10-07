"""Pinned messages of the open chat, newest first, for the bar under the chat header. Qt-free.

Loaded with searchChatMessages (filter Pinned) when the chat opens, kept current from
updateMessageIsPinned. The bar shows one at a time: clicking it jumps to that message and moves
on to the next older one, round and round, like other clients.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ..td.client import Event, TdClient, TdError

log = logging.getLogger(__name__)

Message = dict[str, Any]
MAX_PINNED = 100


class PinnedMessages:
    def __init__(self, client: TdClient, chat_id: int, on_change: Callable[[], None],
                 topic: dict[str, Any] | None = None) -> None:
        """`topic`: a forum topic (MessageTopic): only its pinned messages."""
        self._client = client
        self.chat_id = chat_id
        self._topic = topic
        self._on_change = on_change
        self.messages: list[Message] = []  # newest first
        self.index = 0  # the one the bar shows
        self._disposed = False
        self._refresh_task: asyncio.Task[Any] | None = None
        self._unsubscribe = [
            client.on("updateMessageIsPinned", self._on_pinned),
            client.on("updateDeleteMessages", self._on_deleted),
            client.on("updateMessageContent", self._on_content),
        ]

    @property
    def current(self) -> Message | None:
        return self.messages[self.index] if self.messages else None

    def dispose(self) -> None:
        self._disposed = True
        for off in self._unsubscribe:
            off()
        self._unsubscribe = []
        if self._refresh_task is not None:
            self._refresh_task.cancel()

    def advance(self) -> Message | None:
        """The bar was clicked: return the message it showed, show the next older one."""
        shown = self.current
        if self.messages:
            self.index = (self.index + 1) % len(self.messages)
            self._on_change()
        return shown

    def show(self, message_id: int) -> None:
        """Scrolled to a pinned message some other way: the bar follows."""
        for index, message in enumerate(self.messages):
            if message["id"] == message_id and index != self.index:
                self.index = index
                self._on_change()

    async def load(self) -> None:
        try:
            found = await self._client.send({
                "@type": "searchChatMessages", "chat_id": self.chat_id, "topic_id": self._topic,
                "query": "", "sender_id": None, "from_message_id": 0, "offset": 0,
                "limit": MAX_PINNED, "filter": {"@type": "searchMessagesFilterPinned"}})
            messages = [m for m in found.get("messages") or [] if m]
        except TdError as e:
            log.info("Pinned messages via search failed (%s), asking for the last one", e)
            try:
                messages = [await self._client.send({"@type": "getChatPinnedMessage",
                                                     "chat_id": self.chat_id})]
            except TdError:
                messages = []
        if self._disposed:
            return
        current = self.current["id"] if self.current else 0
        self.messages = sorted(messages, key=lambda m: m["id"], reverse=True)
        ids = [m["id"] for m in self.messages]
        self.index = ids.index(current) if current in ids else 0
        self._on_change()

    def _refresh(self) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.ensure_future(self.load())

    def _on_pinned(self, event: Event) -> None:
        if event.get("chat_id") == self.chat_id:
            self._refresh()

    def _on_deleted(self, event: Event) -> None:
        if event.get("chat_id") != self.chat_id or event.get("from_cache"):
            return
        gone = set(event.get("message_ids") or ())
        if any(m["id"] in gone for m in self.messages):
            self.messages = [m for m in self.messages if m["id"] not in gone]
            self.index = min(self.index, max(0, len(self.messages) - 1))
            self._on_change()

    def _on_content(self, event: Event) -> None:
        if event.get("chat_id") != self.chat_id:
            return
        for index, message in enumerate(self.messages):
            if message["id"] == event.get("message_id"):
                self.messages[index] = {**message, "content": event["new_content"]}
                self._on_change()
