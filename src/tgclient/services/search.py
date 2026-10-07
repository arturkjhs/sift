"""Message search: local index (keywords + meaning) merged with TDLib's server-side search.

Indexing:
- live: new, edited, sent and deleted messages from TDLib updates; voice transcripts when ready;
- backfill: a slow background walk back through the history of the most recent chats
  (bounded per chat by count and age, paced, resumable, backs off on FLOOD_WAIT);
- vectors: when meaning-based search is on, texts are embedded locally in a worker thread.
Secret chats are never indexed.

Search runs three rankers and fuses them with reciprocal rank fusion: local FTS5, local vectors,
and TDLib searchMessages (covers history that isn't indexed yet).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..store.chats import ARCHIVE, MAIN, ChatStore
from ..store.users import UserStore
from ..td.client import Event, TdClient, TdError
from .ai import AiService
from .embeddings import Embedder
from .search_index import Doc, Key, Progress, SearchIndex
from .summary import sender_key, sender_object

log = logging.getLogger(__name__)

Message = dict[str, Any]
Listener = Callable[[], None]

PAGE = 100
PACE_SECONDS = 0.4
MAX_PER_CHAT = 3000
MAX_AGE_DAYS = 365
MAX_CHATS = 150
MIN_EMBED_CHARS = 12
EMBED_BATCH = 64
RRF_K = 60
SEMANTIC_MIN_SCORE = 0.3

_FLOOD_WAIT = re.compile(r"retry after (\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Hit:
    chat_id: int
    message_id: int
    date: int
    sender: str  # display name
    text: str
    keyword: bool  # matched words (locally or on the server)
    semantic: bool  # matched by meaning


@dataclass(frozen=True)
class Status:
    docs: int = 0
    chats: int = 0
    vectors: int = 0
    indexing: bool = False
    model_state: str = ""  # "" | loading | ready | error
    model_error: str = ""


def searchable_text(message: Message, transcript: str | None = None) -> str:
    """Text worth indexing: message text, captions, file names, poll questions, transcripts."""
    content = message.get("content") or {}
    parts: list[str] = []

    def text_of(value: Any) -> str:
        return value.get("text", "") if isinstance(value, dict) else (value or "")

    match content.get("@type"):
        case "messageText":
            parts.append(text_of(content.get("text")))
        case "messageDocument":
            parts.append((content.get("document") or {}).get("file_name", ""))
        case "messageAudio":
            audio = content.get("audio") or {}
            parts += [audio.get("performer", ""), audio.get("title", "")]
        case "messagePoll":
            parts.append(text_of((content.get("poll") or {}).get("question")))
    parts.append(text_of(content.get("caption")))
    if transcript:
        parts.append(transcript)
    return " ".join(" ".join(p.split()) for p in parts if p).strip()


def fuse(rankings: list[list[Key]], k: int = RRF_K) -> list[Key]:
    scores: dict[Key, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda key: -scores[key])


class SearchService:
    def __init__(
        self,
        client: TdClient,
        chats: ChatStore,
        users: UserStore,
        index: SearchIndex,
        embedder: Embedder | None,
        ai: AiService | None = None,
    ) -> None:
        self._client = client
        self._chats = chats
        self._users = users
        self._index = index
        self._embedder = embedder
        self._ai = ai
        self._pending: dict[Key, Doc | None] = {}  # None: remove
        self._wake = asyncio.Event()
        self._embed_queue: list[tuple[int, str]] = []
        self._embed_wake = asyncio.Event()
        self._listeners: list[Listener] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._started = False
        self._closed = False
        self._indexing = False
        self._stats = index.stats()
        self._model_state = ""
        self._model_error = ""
        self._dim = 0
        self._semantic = embedder is not None and index.meta("semantic") == "1"
        # Pacing (tests shorten these).
        self.start_delay = 3.0  # let login and the first screen settle before backfill
        self.pace = PACE_SECONDS
        self.flush_delay = 0.5

        handlers: dict[str, Callable[[Event], None]] = {
            "updateNewMessage": self._on_new_message,
            "updateMessageSendSucceeded": self._on_send_succeeded,
            "updateMessageContent": self._on_content,
            "updateDeleteMessages": self._on_delete,
        }
        for update_type, handler in handlers.items():
            client.on(update_type, handler)
        if ai is not None:
            ai.subscribe(self._on_ai)

    # --- lifecycle and settings -------------------------------------------------------------

    def start(self) -> None:
        """Call after login: starts writing, backfill and (if on) the embedding model."""
        if self._started:
            return
        self._started = True
        self._spawn(self._writer())
        self._spawn(self._backfill())
        self._spawn(self._embed_worker())
        if self._semantic:
            self._spawn(self._load_model())

    async def close(self) -> None:
        self._closed = True
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await asyncio.to_thread(self._index.close)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @property
    def semantic_supported(self) -> bool:
        return self._embedder is not None

    @property
    def semantic_enabled(self) -> bool:
        return self._semantic

    @property
    def embedding_model(self) -> str:
        return self._embedder.model if self._embedder else ""

    def set_semantic(self, enabled: bool) -> None:
        if self._embedder is None or enabled == self._semantic:
            return
        self._semantic = enabled
        self._spawn(asyncio.to_thread(self._index.set_meta, "semantic", "1" if enabled else "0"))
        if enabled and self._started:
            self._spawn(self._load_model())
        self._notify()

    def status(self) -> Status:
        return Status(self._stats.docs, self._stats.chats, self._stats.vectors, self._indexing,
                      self._model_state if self._semantic else "", self._model_error)

    # --- search -----------------------------------------------------------------------------

    async def search(self, query: str, chat_id: int = 0, sender: str = "",
                     limit: int = 40) -> list[Hit]:
        query = " ".join(query.split())
        if len(query) < 2:
            return []
        local_keyword, semantic, server = await asyncio.gather(
            asyncio.to_thread(self._index.keyword, query, chat_id, sender),
            self._semantic_search(query, chat_id, sender),
            self._server_search(query, chat_id, sender),
        )
        server_keys = [(m["chat_id"], m["id"]) for m in server]
        semantic_keys = [key for key, _ in semantic]
        ranked = fuse([local_keyword, semantic_keys, server_keys])
        keyword_hits = set(local_keyword) | set(server_keys)
        semantic_hits = set(semantic_keys)

        known = {(m["chat_id"], m["id"]): m for m in server}
        messages = await self._resolve([k for k in ranked if k not in known], limit * 2)
        known.update(messages)
        hits: list[Hit] = []
        for key in ranked:
            message = known.get(key)
            if message is None or not self._indexable_chat(key[0]):
                continue
            text = searchable_text(message, self._transcript(key)) or "…"
            hits.append(Hit(key[0], key[1], message.get("date", 0), self._sender_name(message),
                            text, key in keyword_hits, key in semantic_hits))
            if len(hits) >= limit:
                break
        return hits

    async def _semantic_search(self, query: str, chat_id: int,
                               sender: str) -> list[tuple[Key, float]]:
        if not self._semantic or self._model_state != "ready" or self._embedder is None:
            return []
        try:
            vector = await asyncio.to_thread(self._embedder.embed_query, query)
        except Exception:
            log.exception("Query embedding failed")
            return []
        return await asyncio.to_thread(
            self._index.semantic, vector, chat_id, sender, 100, SEMANTIC_MIN_SCORE)

    async def _server_search(self, query: str, chat_id: int, sender: str) -> list[Message]:
        if sender and not chat_id:
            return []  # searchMessages has no sender filter: the local index alone
        if chat_id:
            request: dict[str, Any] = {
                "@type": "searchChatMessages", "chat_id": chat_id, "topic_id": None,
                "query": query, "sender_id": sender_object(sender) if sender else None,
                "from_message_id": 0, "offset": 0, "limit": 50, "filter": None,
            }
        else:
            request = {
                "@type": "searchMessages", "chat_list": None, "query": query, "offset": "",
                "limit": 50, "filter": None, "chat_type_filter": None, "min_date": 0,
                "max_date": 0,
            }
        try:
            result = await self._client.send(request)
        except TdError as e:
            log.debug("%s failed: %s", request["@type"], e)
            return []
        return [m for m in result.get("messages") or [] if m]

    async def _resolve(self, keys: list[Key], limit: int) -> dict[Key, Message]:
        """Fetch messages for index hits from TDLib; forget the ones that no longer exist."""
        by_chat: dict[int, list[int]] = {}
        for chat_id, message_id in keys[:limit]:
            by_chat.setdefault(chat_id, []).append(message_id)

        async def fetch(chat_id: int, ids: list[int]) -> list[tuple[int, Message | None]]:
            try:
                result = await self._client.send(
                    {"@type": "getMessages", "chat_id": chat_id, "message_ids": ids})
            except TdError:
                return []
            return list(zip(ids, result.get("messages") or [], strict=False))

        found: dict[Key, Message] = {}
        for chat_id, pairs in zip(by_chat, await asyncio.gather(
                *(fetch(c, ids) for c, ids in by_chat.items())), strict=True):
            gone = []
            for message_id, message in pairs:
                if message:
                    found[(chat_id, message_id)] = message
                else:
                    gone.append(message_id)
            for message_id in gone:
                self._queue_remove((chat_id, message_id))
        return found

    # --- live updates -----------------------------------------------------------------------

    def _on_new_message(self, event: Event) -> None:
        self._queue(event["message"])

    def _on_send_succeeded(self, event: Event) -> None:
        message = event["message"]
        self._queue_remove((message.get("chat_id", 0), event["old_message_id"]))
        self._queue(message)

    def _on_content(self, event: Event) -> None:
        if self._indexable_chat(event.get("chat_id", 0)):
            self._spawn(self._refetch(event["chat_id"], event["message_id"]))

    def _on_delete(self, event: Event) -> None:
        if event.get("from_cache"):
            return
        for message_id in event.get("message_ids", []):
            self._queue_remove((event.get("chat_id", 0), message_id))

    def _on_ai(self, kind: str, payload: Any) -> None:
        if kind == "transcript" and self._ai is not None:
            chat_id, message_id = payload
            transcript = self._ai.transcript(chat_id, message_id)
            if transcript and transcript.state == "done" and self._indexable_chat(chat_id):
                self._spawn(self._refetch(chat_id, message_id))

    async def _refetch(self, chat_id: int, message_id: int) -> None:
        try:
            message = await self._client.send(
                {"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
        except TdError:
            return
        self._queue(message)

    def _queue(self, message: Message) -> None:
        chat_id = message.get("chat_id", 0)
        if not self._indexable_chat(chat_id) or message.get("sending_state"):
            return  # pending messages get a new id when sent
        key = (chat_id, message["id"])
        text = searchable_text(message, self._transcript(key))
        if not text:
            return
        self._pending[key] = Doc(chat_id, message["id"], sender_key(message),
                                 message.get("date", 0), text)
        self._wake.set()

    def _queue_remove(self, key: Key) -> None:
        self._pending[key] = None
        self._wake.set()

    # --- background work --------------------------------------------------------------------

    async def _writer(self) -> None:
        """Batches index writes: one transaction per second at most."""
        while not self._closed:
            await self._wake.wait()
            self._wake.clear()
            await asyncio.sleep(self.flush_delay)
            await self._flush()

    async def _flush(self) -> None:
        pending, self._pending = self._pending, {}
        if not pending:
            return
        docs = [doc for doc in pending.values() if doc is not None]
        removed: dict[int, list[int]] = {}
        for (chat_id, message_id), doc in pending.items():
            if doc is None:
                removed.setdefault(chat_id, []).append(message_id)
        ids = await asyncio.to_thread(self._index.upsert, docs) if docs else []
        for chat_id, message_ids in removed.items():
            await asyncio.to_thread(self._index.remove, chat_id, message_ids)
        if self._semantic:
            self._embed_queue.extend(
                (doc_id, doc.text) for doc_id, doc in zip(ids, docs, strict=True)
                if len(doc.text) >= MIN_EMBED_CHARS)
            self._embed_wake.set()
        await self._refresh_stats()

    async def _backfill(self) -> None:
        await asyncio.sleep(self.start_delay)
        while not self._closed:
            chat_id = await self._next_backfill_chat()
            if chat_id is None:
                self._set_indexing(False)
                await asyncio.sleep(60)
                continue
            self._set_indexing(True)
            await self._backfill_page(chat_id)
            await asyncio.sleep(self.pace)

    async def _next_backfill_chat(self) -> int | None:
        candidates = (self._chats.chats_in(MAIN) + self._chats.chats_in(ARCHIVE))[:MAX_CHATS]
        for chat in candidates:
            if not self._indexable_chat(chat.id):
                continue
            progress = await asyncio.to_thread(self._index.progress, chat.id)
            if not progress.done:
                return chat.id
        return None

    async def _backfill_page(self, chat_id: int) -> None:
        progress = await asyncio.to_thread(self._index.progress, chat_id)
        try:
            page = await self._client.send({
                "@type": "getChatHistory", "chat_id": chat_id,
                "from_message_id": progress.oldest_id, "offset": 0, "limit": PAGE,
                "only_local": False,
            })
        except TdError as e:
            wait = _FLOOD_WAIT.search(e.message)
            if e.code == 429 and wait:
                log.info("Search backfill: flood wait %ss", wait.group(1))
                await asyncio.sleep(int(wait.group(1)) + 1)
            else:  # e.g. no access to the chat anymore: skip it
                await asyncio.to_thread(self._index.set_progress, chat_id,
                                        Progress(progress.oldest_id, progress.count, True))
            return
        messages = [m for m in page.get("messages") or []
                    if m and m["id"] != progress.oldest_id]
        cutoff = time.time() - MAX_AGE_DAYS * 86_400
        for message in messages:
            self._queue(message)
        count = progress.count + len(messages)
        oldest = messages[-1]["id"] if messages else progress.oldest_id
        done = (not messages or count >= MAX_PER_CHAT
                or messages[-1].get("date", 0) < cutoff)
        await asyncio.to_thread(self._index.set_progress, chat_id, Progress(oldest, count, done))

    async def _load_model(self) -> None:
        if self._embedder is None or self._model_state in ("loading", "ready"):
            return
        self._model_state, self._model_error = "loading", ""
        self._notify()
        try:
            await asyncio.to_thread(self._embedder.load)
            probe = await asyncio.to_thread(self._embedder.embed_query, "dimension probe")
            self._dim = len(probe)
            await asyncio.to_thread(self._index.load_vectors, self._embedder.model)
        except Exception as e:
            log.exception("Loading the embedding model failed")
            self._model_state, self._model_error = "error", str(e) or type(e).__name__
            self._notify()
            return
        self._model_state = "ready"
        self._embed_wake.set()
        await self._refresh_stats()

    async def _embed_worker(self) -> None:
        """Embeds queued texts, then the backlog of indexed docs that have no vector yet."""
        while not self._closed:
            await self._embed_wake.wait()
            self._embed_wake.clear()
            while (self._semantic and self._model_state == "ready" and self._embedder
                   and not self._closed):
                batch = self._embed_queue[:EMBED_BATCH]
                del self._embed_queue[:EMBED_BATCH]
                if not batch:
                    batch = await self._backlog_batch()
                    if not batch:
                        break
                ids = [doc_id for doc_id, _ in batch]
                try:
                    vectors = await asyncio.to_thread(
                        self._embedder.embed_passages, [text for _, text in batch])
                except Exception:
                    log.exception("Embedding failed")
                    break
                await asyncio.to_thread(self._index.add_vectors, self._embedder.model, ids,
                                        np.asarray(vectors))
                await self._refresh_stats()

    async def _backlog_batch(self) -> list[tuple[int, str]]:
        """Docs indexed before meaning-based search was on: texts come back from TDLib."""
        assert self._embedder is not None
        keys = await asyncio.to_thread(self._index.missing_vectors, self._embedder.model,
                                       EMBED_BATCH)
        if not keys:
            return []
        messages = await self._resolve(keys, len(keys))
        ids = await asyncio.to_thread(self._index.doc_ids, keys)
        batch: list[tuple[int, str]] = []
        short: list[int] = []
        for key, doc_id in zip(keys, ids, strict=True):
            message = messages.get(key)
            if doc_id is None or message is None:
                continue
            text = searchable_text(message, self._transcript(key))
            if len(text) >= MIN_EMBED_CHARS:
                batch.append((doc_id, text))
            else:
                short.append(doc_id)
        if short:  # too short to mean anything: a zero vector never matches and isn't retried
            await asyncio.to_thread(self._index.add_vectors, self._embedder.model, short,
                                    np.zeros((len(short), self._dim), dtype=np.float32))
        return batch

    # --- helpers ----------------------------------------------------------------------------

    def _indexable_chat(self, chat_id: int) -> bool:
        chat = self._chats.chats.get(chat_id)
        return chat is not None and chat.type != "secret"

    def _transcript(self, key: Key) -> str | None:
        if self._ai is None:
            return None
        transcript = self._ai.transcript(*key)
        return transcript.text if transcript and transcript.state == "done" else None

    def _sender_name(self, message: Message) -> str:
        sender = message.get("sender_id") or {}
        if sender.get("@type") == "messageSenderUser":
            user = self._users.users.get(sender.get("user_id", 0))
            return user.full_name if user else ""
        chat = self._chats.chats.get(sender.get("chat_id", 0))
        return chat.title if chat else ""

    def _set_indexing(self, value: bool) -> None:
        if value != self._indexing:
            self._indexing = value
            self._notify()

    async def _refresh_stats(self) -> None:
        self._stats = await asyncio.to_thread(self._index.stats)
        self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                log.exception("Search listener failed")

    def _spawn(self, awaitable: Any) -> None:
        task = asyncio.ensure_future(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("Search task failed", exc_info=task.exception())
