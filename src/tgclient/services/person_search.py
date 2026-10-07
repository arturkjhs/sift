"""Messages of one sender: in a chat (searchChatMessages with sender_id, server-side, so it
finds what the local index hasn't seen), and across the chats in common with a person.
Qt-free; the Qt model is models/person_messages.py, the AI check uses it for candidates.

An empty query with sender_id lists all of the sender's messages (TDLib serves it: the person
summary relies on it too). If TDLib refuses an empty query, the chat's history is paged and
filtered by sender instead (slower, bounded).

Secret chats are left out: TDLib doesn't support sender_id there.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..store.chats import ChatStore
from ..td.client import TdClient, TdError
from .summary import sender_object

log = logging.getLogger(__name__)

Message = dict[str, Any]
PAGE = 50
FIRST_PAGE_PER_CHAT = 20  # common chats: the first page of each
PARALLEL = 3  # common chats searched at once
FALLBACK_PAGES = 20  # history pages scanned when an empty query isn't accepted
_FLOOD_WAIT = re.compile(r"retry after (\d+)", re.IGNORECASE)


@dataclass
class ChatResults:
    chat_id: int
    messages: list[Message] = field(default_factory=list)  # newest first
    next_from: int = 0  # more from this message id (0: no more)


class PersonSearch:
    def __init__(self, client: TdClient, chats: ChatStore) -> None:
        self._client = client
        self._chats = chats
        self._scan_from: dict[tuple[int, str], int] = {}  # fallback paging position

    def searchable(self, chat_id: int) -> bool:
        chat = self._chats.chats.get(chat_id)
        return chat is not None and chat.type != "secret"

    async def page(self, chat_id: int, sender: str, query: str = "", from_id: int = 0,
                   limit: int = PAGE, topic: dict[str, Any] | None = None
                   ) -> tuple[list[Message], int]:
        """One page of the sender's messages in a chat, newest first; returns (messages,
        next from_message_id or 0 at the end)."""
        if not self.searchable(chat_id):
            return [], 0
        request = {"@type": "searchChatMessages", "chat_id": chat_id, "topic_id": topic,
                   "query": query, "sender_id": sender_object(sender),
                   "from_message_id": from_id, "offset": 0, "limit": limit, "filter": None}
        try:
            found = await self._send(request)
        except TdError as e:
            if query.strip():
                log.info("searchChatMessages(%s, %s) failed: %s", chat_id, sender, e)
                return [], 0
            log.info("Empty query with sender_id refused (%s): scanning history", e)
            return await self._scan(chat_id, sender, from_id, limit)
        messages = [m for m in found.get("messages") or [] if m and m["id"] != from_id]
        next_from = int(found.get("next_from_message_id") or 0) if messages else 0
        return messages, next_from

    async def common_chats(self, user_id: int) -> list[int]:
        """Groups in common with a user (all pages) and the private chat with them."""
        found: list[int] = []
        offset = 0
        for _ in range(10):
            try:
                page = await self._send({"@type": "getGroupsInCommon", "user_id": user_id,
                                         "offset_chat_id": offset, "limit": 100})
            except TdError as e:
                log.info("getGroupsInCommon failed: %s", e)
                break
            ids = [int(i) for i in page.get("chat_ids") or [] if i not in found]
            if not ids:
                break
            found.extend(ids)
            offset = ids[-1]
        private = user_id if user_id in self._chats.chats else 0
        if not private:
            try:
                private = int((await self._send({"@type": "createPrivateChat",
                                                 "user_id": user_id, "force": False}))["id"])
            except (TdError, KeyError) as e:
                log.info("No private chat with %s: %s", user_id, e)
        if private and private not in found:
            found.insert(0, private)
        return [c for c in found if self.searchable(c)]

    async def in_chats(self, chat_ids: list[int], sender: str, query: str = "",
                       limit: int = FIRST_PAGE_PER_CHAT,
                       on_chat: Callable[[ChatResults], None] | None = None
                       ) -> list[ChatResults]:
        """The first page in each chat, at most PARALLEL requests at a time. `on_chat` gets
        each chat's results as they come (the UI fills in progressively)."""
        gate = asyncio.Semaphore(PARALLEL)

        async def one(chat_id: int) -> ChatResults:
            async with gate:
                messages, next_from = await self.page(chat_id, sender, query, 0, limit)
            result = ChatResults(chat_id, messages, next_from)
            if on_chat is not None:
                on_chat(result)
            return result

        return list(await asyncio.gather(*(one(c) for c in chat_ids)))

    async def _send(self, request: dict[str, Any]) -> dict[str, Any]:
        """FLOOD_WAIT: wait as long as Telegram asks (up to a minute), once."""
        try:
            return await self._client.send(request)
        except TdError as e:
            wait = _FLOOD_WAIT.search(e.message or "")
            if e.code != 429 or wait is None or int(wait.group(1)) > 60:
                raise
            await asyncio.sleep(int(wait.group(1)) + 0.5)
            return await self._client.send(request)

    async def _scan(self, chat_id: int, sender: str, from_id: int, limit: int
                    ) -> tuple[list[Message], int]:
        target = sender_object(sender) or {}
        found: list[Message] = []
        cursor = from_id
        for _ in range(FALLBACK_PAGES):
            try:
                page = await self._send({"@type": "getChatHistory", "chat_id": chat_id,
                                         "from_message_id": cursor, "offset": 0, "limit": 100,
                                         "only_local": False})
            except TdError:
                return found, 0
            batch = [m for m in page.get("messages") or [] if m and m["id"] != cursor]
            if not batch:
                return found, 0
            found.extend(m for m in batch if _same_sender(m.get("sender_id") or {}, target))
            cursor = batch[-1]["id"]
            if len(found) >= limit:
                return found[:limit], found[limit - 1]["id"]
        return found, cursor


def _same_sender(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a.get("@type") == b.get("@type")
            and (a.get("user_id") or a.get("chat_id")) == (b.get("user_id") or b.get("chat_id")))
