"""LLM features: voice transcription and chat summaries through OpenRouter. Qt-free.

Privacy rules enforced here, not in the UI:
- AI is off for every chat until the user turns it on for that chat.
- Secret chats can never be turned on and are never sent anywhere.
- Nothing is sent without an explicit user action (click on "Transcribe" / "Summarize").
- Requests go only to zero-data-retention providers (see openrouter.py).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..store.chats import ChatStore
from ..store.files import USER_PRIORITY
from ..store.users import UserStore
from ..td.client import TdClient, TdError
from . import summary as summaries
from .ai_store import AiStore, StoredSummary, SummaryKey
from .openrouter import OpenRouter, OpenRouterError

log = logging.getLogger(__name__)

ChangeKind = Literal["enabled", "transcript", "summary", "config"]
Listener = Callable[[ChangeKind, Any], None]

MAX_VOICE_BYTES = 20 * 1024 * 1024
TRANSCRIBE_PROMPT = (
    "Transcribe this voice message verbatim in its original language. "
    "Output only the transcript text, without quotes or comments. "
    "If there is no speech, output an empty string."
)


class AiUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Transcript:
    state: str  # pending | done | error
    text: str = ""


@dataclass(frozen=True)
class SummaryState:
    state: str  # "" | pending | done | error
    scope: str = ""
    text: str = ""  # markdown with tgc://message/<id> links
    error: str = ""
    created: int = 0
    count: int = 0
    truncated: bool = False
    name: str = ""  # person summaries: who it is about


class AiService:
    def __init__(
        self,
        client: TdClient,
        chats: ChatStore,
        users: UserStore,
        store: AiStore,
        router: OpenRouter | None,
        summary_model: str,
        transcription_model: str,
        key_hint: str = "",
        key_source: str = "",
    ) -> None:
        self._client = client
        self._chats = chats
        self._users = users
        self._store = store
        self._router = router
        self.key_hint = key_hint  # masked key for display, never the key itself
        self.key_source = key_source  # "environment" | "settings" | ""
        self.summary_model = summary_model
        self.transcription_model = transcription_model
        self._transcripts: dict[tuple[int, int], Transcript] = {
            key: Transcript("done", text) for key, text in store.transcripts.items()
        }
        self._summaries: dict[SummaryKey, SummaryState] = {
            key: SummaryState("done", s.scope, s.text, created=s.created, name=s.name)
            for key, s in store.summaries.items()
        }
        self._listeners: list[Listener] = []
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def configured(self) -> bool:
        return self._router is not None

    def use_router(self, router: OpenRouter | None, key_hint: str = "",
                   key_source: str = "") -> None:
        """Switch to another API key at runtime (Settings). Running requests finish on the
        old client, which is closed afterwards."""
        old, self._router = self._router, router
        self.key_hint, self.key_source = key_hint, key_source
        if old is not None and old is not router:
            self._spawn(self._close_later(old))
        self._emit("config", None)

    async def _close_later(self, router: OpenRouter) -> None:
        await asyncio.sleep(300)  # let in-flight summaries finish
        await router.aclose()

    # --- per-chat switch --------------------------------------------------------------------

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def allowed(self, chat_id: int) -> bool:
        """Whether AI can be turned on for this chat at all (never for secret chats)."""
        chat = self._chats.chats.get(chat_id)
        return chat is not None and chat.type != "secret"

    def is_enabled(self, chat_id: int) -> bool:
        return chat_id in self._store.enabled and self.allowed(chat_id)

    def enabled_chats(self) -> list[int]:
        return sorted(c for c in self._store.enabled if self.allowed(c))

    def set_enabled(self, chat_id: int, enabled: bool) -> bool:
        if enabled and not self.allowed(chat_id):
            return False
        if enabled == (chat_id in self._store.enabled):
            return True
        if enabled:
            self._store.enabled.add(chat_id)
        else:
            self._store.enabled.discard(chat_id)
        self._spawn(asyncio.to_thread(self._store.save_enabled, chat_id, enabled))
        self._emit("enabled", chat_id)
        return True

    # --- transcription ----------------------------------------------------------------------

    def transcript(self, chat_id: int, message_id: int) -> Transcript | None:
        return self._transcripts.get((chat_id, message_id))

    def transcribe(self, chat_id: int, message_id: int) -> None:
        """Start transcribing a voice message (no-op if done or running)."""
        key = (chat_id, message_id)
        current = self._transcripts.get(key)
        if current is not None and current.state in ("pending", "done"):
            return
        try:
            self._check(chat_id)
        except AiUnavailable as e:
            self._set_transcript(key, Transcript("error", str(e)))
            return
        self._set_transcript(key, Transcript("pending"))
        self._spawn(self._transcribe(chat_id, message_id))

    async def _transcribe(self, chat_id: int, message_id: int) -> None:
        key = (chat_id, message_id)
        try:
            message = await self._client.send(
                {"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
            content = message.get("content", {})
            if content.get("@type") != "messageVoiceNote":
                raise AiUnavailable("Only voice messages can be transcribed")
            file = content.get("voice_note", {}).get("voice") or {}
            size = file.get("size") or file.get("expected_size") or 0
            if size > MAX_VOICE_BYTES:
                raise AiUnavailable("Voice message is too long to transcribe")
            files = self._chats.files
            files.register(file)
            path = await files.fetch(file["id"], USER_PRIORITY)
            audio = await asyncio.to_thread(Path(path).read_bytes)
            self._check(chat_id)  # the user may have turned AI off meanwhile
            assert self._router is not None
            text = await self._router.transcribe(
                self.transcription_model, audio, "ogg", TRANSCRIBE_PROMPT)
        except (AiUnavailable, OpenRouterError) as e:
            self._set_transcript(key, Transcript("error", str(e)))
            return
        except TdError as e:
            self._set_transcript(key, Transcript("error", f"Telegram: {e.message}"))
            return
        except TimeoutError:
            self._set_transcript(key, Transcript("error", "Download timed out"))
            return
        except Exception:
            log.exception("Transcription failed")
            self._set_transcript(key, Transcript("error", "Unexpected error, see the log"))
            return
        text = text or "(no speech)"
        self._store.transcripts[key] = text
        self._set_transcript(key, Transcript("done", text))
        await asyncio.to_thread(
            self._store.save_transcript, chat_id, message_id, text, self.transcription_model)

    def _set_transcript(self, key: tuple[int, int], value: Transcript) -> None:
        self._transcripts[key] = value
        self._emit("transcript", key)

    # --- summaries --------------------------------------------------------------------------
    # Keyed by (chat_id, subject): subject "" is the whole chat, "user:<id>" one person in it.

    def summary(self, chat_id: int, subject: str = "") -> SummaryState:
        return self._summaries.get((chat_id, subject)) or SummaryState("")

    def summarize(self, chat_id: int, scope: str) -> None:
        """Summary of the chat's recent messages (unread | day | week)."""
        if scope not in summaries.SCOPES:
            raise ValueError(f"Unknown summary scope: {scope}")
        self._start_summary((chat_id, ""), scope, "")

    def summarize_person(self, chat_id: int, sender: str, name: str) -> None:
        """Profile of one participant, built from their own messages in this chat."""
        if summaries.sender_object(sender) is None:
            raise ValueError(f"Bad sender key: {sender}")
        self._start_summary((chat_id, sender), "person", name)

    def _start_summary(self, key: SummaryKey, scope: str, name: str) -> None:
        chat_id = key[0]
        previous = self.summary(*key)
        if previous.state == "pending":
            return
        try:
            self._check(chat_id)
        except AiUnavailable as e:
            self._set_summary(key, SummaryState("error", scope, error=str(e), name=name))
            return
        self._set_summary(key, SummaryState("pending", scope, previous.text,
                                            created=previous.created, name=name))
        self._spawn(self._summarize(key, scope, name))

    async def _summarize(self, key: SummaryKey, scope: str, name: str) -> None:
        chat_id, subject = key
        chat = self._chats.chats[chat_id]
        try:
            if subject:
                messages, truncated = await summaries.collect_from_sender(
                    self._client, chat_id, subject)
            else:
                outside = summaries.stop_condition(scope, chat.last_read_inbox_message_id)
                messages, truncated = await summaries.collect(self._client, chat_id, outside)
            text, source = summaries.render(
                messages, self._sender_name,
                lambda m: self._store.transcripts.get((chat_id, m["id"])))
            if not source.count:
                raise AiUnavailable(
                    "No messages from this person here" if subject
                    else "No unread messages" if scope == "unread" else "Nothing to summarize")
            self._check(chat_id)
            assert self._router is not None
            prompt = (summaries.person_prompt(chat.title, name, self._reader(), text) if subject
                      else summaries.prompt(chat.title, self._reader(), text))
            reply = await self._router.complete(self.summary_model, prompt)
        except (AiUnavailable, OpenRouterError) as e:
            self._set_summary(key, SummaryState("error", scope, error=str(e), name=name))
            return
        except Exception:
            log.exception("Summary failed")
            self._set_summary(key, SummaryState("error", scope, name=name,
                                                error="Unexpected error, see the log"))
            return
        result = SummaryState(
            "done", scope, summaries.linkify(reply, source), created=int(time.time()),
            count=source.count, truncated=truncated or source.truncated, name=name)
        self._set_summary(key, result)
        stored = StoredSummary(scope, result.text, self.summary_model, result.created, name)
        self._store.summaries[key] = stored
        await asyncio.to_thread(self._store.save_summary, key, stored)

    def _set_summary(self, key: SummaryKey, value: SummaryState) -> None:
        self._summaries[key] = value
        self._emit("summary", key)

    # --- internals --------------------------------------------------------------------------

    def _check(self, chat_id: int) -> None:
        if not self.allowed(chat_id):
            raise AiUnavailable("AI is never used in secret chats")
        if not self.is_enabled(chat_id):
            raise AiUnavailable("AI is turned off for this chat")
        if self._router is None:
            raise AiUnavailable("Add an OpenRouter API key in Settings to use AI features")

    def _sender_name(self, message: dict[str, Any]) -> str:
        sender = message.get("sender_id", {})
        if sender.get("@type") == "messageSenderUser":
            user = self._users.users.get(sender.get("user_id", 0))
            return user.full_name if user else ""
        chat = self._chats.chats.get(sender.get("chat_id", 0))
        return chat.title if chat else ""

    def _reader(self) -> str:
        me = self._users.users.get(self._users.my_id or 0)
        return me.full_name if me else ""

    def _emit(self, kind: ChangeKind, payload: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:
                log.exception("AI listener failed")

    def _spawn(self, awaitable: Any) -> None:
        task = asyncio.ensure_future(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("AI task failed", exc_info=task.exception())

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._router is not None:
            await self._router.aclose()
        self._store.close()
