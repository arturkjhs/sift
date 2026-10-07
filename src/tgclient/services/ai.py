"""LLM features through OpenRouter. Qt-free. All privacy rules and spending limits live here.

Rules enforced here, not in the UI:
- AI is off for every chat until the user turns it on for that chat.
- Secret chats can never be turned on and are never sent anywhere.
- Nothing is sent without an explicit user action (Transcribe, Summarize, Translate, Ask...),
  except background features (smart notifications), which need their own per-chat flag on top
  of AI being on; the digest also has its own flag and runs on click.
- Requests go only to zero-data-retention providers (see openrouter.py).
- Every request counts against a monthly per-chat spending limit; identical requests are
  answered from a local cache.

Results ("jobs") are keyed by (chat_id, subject): "" the chat summary, "user:<id>" a person,
"ask" a question about the chat, "events" dates and meetings, "answers:<id>" answers to a
message, "doc:<id>" a question about a file, "explain:<id>" / "reply:<id>" one message explained or
answered (streamed); chat 0 holds results across chats ("digest", "promises").
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ..store.chats import ChatStore
from ..store.files import USER_PRIORITY
from ..store.format import message_body, message_stamp
from ..store.reactions import DEFAULT_REACTIONS, available_keys
from ..store.users import UserStore
from ..td.client import TdClient, TdError
from . import assist
from . import summary as summaries
from .ai_store import AiStore, StoredSummary, SummaryKey, month_start
from .openrouter import Completion, OpenRouter, OpenRouterError

log = logging.getLogger(__name__)

ChangeKind = Literal["enabled", "transcript", "translation", "summary", "config", "flags",
                     "usage"]
Listener = Callable[[ChangeKind, Any], None]
Searcher = Callable[[str, int], Awaitable[list[Any]]]  # (query, chat_id) -> search hits

MAX_VOICE_BYTES = 20 * 1024 * 1024
RECOGNIZE_POLL = 1.0  # seconds between asking for Telegram's recognition result
RECOGNIZE_TIMEOUT = 90.0
TRANSCRIBE_PROMPT = (
    "Transcribe this voice message verbatim in its original language. "
    "Output only the transcript text, without quotes or comments. "
    "If there is no speech, output an empty string."
)
FLAGS = ("digest", "smart_notify")
DIGEST_DEFAULT_HOURS = 24
DIGEST_MAX_DAYS = 7
DIGEST_CHAT_MESSAGES = 300
PROMISE_DAYS = 14
OWN_FOR_PERSON = 300  # own messages searched for replies to the person
ANSWER_WINDOW_SECONDS = 3 * 86400
SMALL_GROUP = 30  # members: up to this, everyone counts as asked
CONTEXT_AROUND = 20  # messages before and after the one explained / replied to
CHAIN_DEPTH = 6  # replied-to messages followed up the chain
STYLE_EXAMPLES = 15  # the user's own messages showing how they write in the chat
CONTEXT_TTL = 120.0  # seconds the collected context is reused (menu count -> request)
MAX_ALLOWED_REACTIONS = 30  # offered to the model for a light message
_PERIODS = {"unread": "unread messages", "day": "last 24 hours", "week": "last 7 days"}


@dataclass
class _ContextParts:
    created: float
    target: dict[str, Any]
    chain: list[dict[str, Any]]
    around: list[dict[str, Any]]


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
    text: str = ""  # markdown with tgc://message/... links
    error: str = ""
    created: int = 0
    count: int = 0
    truncated: bool = False
    name: str = ""  # who or what it is about
    question: str = ""
    cost: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Result:
    text: str  # markdown, citations already linked
    cost: float
    count: int = 0
    truncated: bool = False
    data: dict[str, Any] = field(default_factory=dict)


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
        cheap_model: str = "",
        monthly_limit: float = 1.0,
        translate_to: str = "en",
        read_languages: tuple[str, ...] = (),
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
        self.cheap_model = cheap_model or summary_model  # translation, classification
        self.monthly_limit = monthly_limit  # USD per chat and month; 0 = no limit
        self.translate_to = translate_to
        self.read_languages = tuple(c for c in read_languages if c in assist.LANGUAGES)
        self.searcher: Searcher | None = None  # SearchService.search, set by the app
        self._transcripts: dict[tuple[int, int], Transcript] = {
            key: Transcript("done", text) for key, text in store.transcripts.items()
        }
        self._translations: dict[tuple[int, int, str], Transcript] = {
            key: Transcript("done", text) for key, text in store.translations.items()
        }
        self._summaries: dict[SummaryKey, SummaryState] = {
            key: SummaryState("done", s.scope, s.text, created=s.created, name=s.name,
                              question=s.question, cost=s.cost, data=_json(s.data))
            for key, s in store.summaries.items()
        }
        self._listeners: list[Listener] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._contexts: dict[tuple[int, int], _ContextParts] = {}

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

    def set_models(self, summary: str, cheap: str, transcription: str) -> None:
        self.summary_model = summary.strip() or self.summary_model
        self.cheap_model = cheap.strip() or self.cheap_model
        self.transcription_model = transcription.strip() or self.transcription_model
        self._emit("config", None)

    def set_limit(self, usd: float) -> None:
        self.monthly_limit = max(0.0, usd)
        self._emit("config", None)

    def set_translate_to(self, lang: str) -> None:
        if lang in assist.LANGUAGES and lang != self.translate_to:
            self.translate_to = lang
            self._emit("config", None)

    def set_read_languages(self, codes: list[str]) -> None:
        """Languages the user reads besides `translate_to`: replies in them aren't translated."""
        codes_ = tuple(c for c in dict.fromkeys(codes) if c in assist.LANGUAGES)
        if codes_ != self.read_languages:
            self.read_languages = codes_
            self._emit("config", None)

    @property
    def reads(self) -> tuple[str, ...]:
        """Every language the user reads, theirs first."""
        return (self.translate_to,
                *(c for c in self.read_languages if c != self.translate_to))

    async def _close_later(self, router: OpenRouter) -> None:
        await asyncio.sleep(300)  # let in-flight summaries finish
        await router.aclose()

    # --- per-chat switches ------------------------------------------------------------------

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

    def flag(self, chat_id: int, name: str) -> bool:
        """A background feature (digest, smart_notify) is on: its own flag AND AI on."""
        return chat_id in self._store.flags.get(name, set()) and self.is_enabled(chat_id)

    def flag_set(self, chat_id: int, name: str) -> bool:
        """The stored flag alone (shown even while AI is off for the chat)."""
        return chat_id in self._store.flags.get(name, set())

    def set_flag(self, chat_id: int, name: str, on: bool) -> bool:
        if name not in FLAGS or (on and not self.allowed(chat_id)):
            return False
        chats = self._store.flags.setdefault(name, set())
        if on == (chat_id in chats):
            return True
        (chats.add if on else chats.discard)(chat_id)
        self._spawn(asyncio.to_thread(self._store.save_flag, chat_id, name, on))
        self._emit("flags", chat_id)
        return True

    def forget(self, chat_id: int) -> None:
        """Turn AI off for a chat and delete everything it produced there."""
        self.set_enabled(chat_id, False)
        for name in FLAGS:
            self._store.flags.get(name, set()).discard(chat_id)
        for mapping in (self._transcripts, self._store.transcripts):
            for key in [k for k in mapping if k[0] == chat_id]:
                del mapping[key]
        for mapping in (self._translations, self._store.translations):
            for key in [k for k in mapping if k[0] == chat_id]:
                del mapping[key]
        for mapping in (self._summaries, self._store.summaries):
            for key in [k for k in mapping if k[0] == chat_id]:
                del mapping[key]
        self._spawn(asyncio.to_thread(self._store.forget_chat, chat_id))
        self._emit("flags", chat_id)
        self._emit("summary", (chat_id, ""))

    # --- spending ---------------------------------------------------------------------------

    def spent(self, chat_id: int) -> float:
        self._roll_month()
        return self._store.spent.get(chat_id, 0.0)

    def spent_total(self) -> float:
        self._roll_month()
        return sum(self._store.spent.values())

    def spending(self) -> list[tuple[int, float]]:
        """(chat id, USD) this month, most expensive first; chat 0 is across chats."""
        self._roll_month()
        return sorted(self._store.spent.items(), key=lambda item: -item[1])

    def _roll_month(self) -> None:
        start = month_start()
        if start != self._store.month:
            self._store.month = start
            self._store.spent.clear()

    def _check_budget(self, chat_id: int) -> None:
        if self.monthly_limit > 0 and self.spent(chat_id) >= self.monthly_limit:
            raise AiUnavailable(
                f"This month's AI limit (${self.monthly_limit:.2f}) is used up"
                + (" for this chat" if chat_id else "") + ". Change it in Settings.")

    async def _complete(
        self, chat_id: int, feature: str, model: str, messages: list[dict[str, Any]],
        temperature: float = 0.2, cache: bool = True,
        plugins: list[dict[str, Any]] | None = None,
    ) -> Completion:
        """The one way to the model: limit check, cache, cost accounting."""
        self._check_budget(chat_id)
        key = assist.cache_key(model, messages, json.dumps(plugins)) if cache else ""
        if key:
            hit = await asyncio.to_thread(self._store.cache_get, key)
            if hit is not None:
                return Completion(hit)
        if self._router is None:
            raise AiUnavailable("Add an OpenRouter API key in Settings to use AI features")
        completion = await self._router.complete(model, messages, temperature, plugins)
        await self._record(chat_id, feature, model, completion)
        if key:
            await asyncio.to_thread(self._store.cache_put, key, completion.text)
        return completion

    async def _record(self, chat_id: int, feature: str, model: str,
                      completion: Completion) -> None:
        self._roll_month()
        self._store.spent[chat_id] = self._store.spent.get(chat_id, 0.0) + completion.cost
        self._emit("usage", chat_id)
        await asyncio.to_thread(self._store.add_usage, chat_id, feature, model,
                                completion.prompt_tokens, completion.completion_tokens,
                                completion.cost)

    # --- transcription ----------------------------------------------------------------------

    def transcript(self, chat_id: int, message_id: int) -> Transcript | None:
        return self._transcripts.get((chat_id, message_id))

    @property
    def premium(self) -> bool:
        """Telegram Premium: voice messages are transcribed by Telegram itself (free, the
        audio never leaves Telegram), without OpenRouter and without AI turned on."""
        me = self._users.me
        return bool(me and me.is_premium)

    def transcribe(self, chat_id: int, message_id: int) -> None:
        """Start transcribing a voice message (no-op if done or running)."""
        key = (chat_id, message_id)
        current = self._transcripts.get(key)
        if current is not None and current.state in ("pending", "done"):
            return
        premium = self.premium and self._chats.chats.get(chat_id) is not None
        if not premium:
            try:
                self._check(chat_id)
            except AiUnavailable as e:
                self._set_transcript(key, Transcript("error", str(e)))
                return
        self._set_transcript(key, Transcript("pending"))
        self._spawn(self._recognize(chat_id, message_id) if premium
                    else self._transcribe(chat_id, message_id))

    async def _recognize(self, chat_id: int, message_id: int) -> None:
        """Telegram's own speech recognition (recognizeSpeech): the result arrives in the
        message's voice_note.speech_recognition_result, asked for until it's there."""
        key = (chat_id, message_id)
        try:
            text = await self._telegram_transcript(chat_id, message_id)
        except TdError as e:
            self._set_transcript(key, Transcript("error", f"Telegram: {e.message}"))
            return
        except TimeoutError:
            self._set_transcript(key, Transcript("error", "Telegram didn't recognize it in time"))
            return
        text = text or "(no speech)"
        self._store.transcripts[key] = text
        self._set_transcript(key, Transcript("done", text))
        await asyncio.to_thread(self._store.save_transcript, chat_id, message_id, text,
                                "telegram")

    async def _telegram_transcript(self, chat_id: int, message_id: int) -> str:
        asked = False
        for _ in range(int(RECOGNIZE_TIMEOUT / RECOGNIZE_POLL)):
            message = await self._client.send({"@type": "getMessage", "chat_id": chat_id,
                                               "message_id": message_id})
            content = message.get("content", {})
            media = content.get("voice_note") or content.get("video_note") or {}
            result = media.get("speech_recognition_result") or {}
            match result.get("@type"):
                case "speechRecognitionResultText":
                    return str(result.get("text", ""))
                case "speechRecognitionResultError":
                    error = result.get("error") or {}
                    raise TdError(error.get("code", 400), error.get("message", "failed"))
            if not asked:
                await self._client.send({"@type": "recognizeSpeech", "chat_id": chat_id,
                                         "message_id": message_id})
                asked = True
            await asyncio.sleep(RECOGNIZE_POLL)
        raise TimeoutError

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
            self._check_budget(chat_id)
            assert self._router is not None
            completion = await self._router.transcribe(
                self.transcription_model, audio, "ogg", TRANSCRIBE_PROMPT)
            await self._record(chat_id, "transcribe", self.transcription_model, completion)
            text = completion.text
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

    # --- translation ------------------------------------------------------------------------

    def translation(self, chat_id: int, message_id: int) -> Transcript | None:
        """Translation of a message into the user's language (Settings), if requested."""
        return self._translations.get((chat_id, message_id, self.translate_to))

    def translate(self, chat_id: int, message_id: int) -> None:
        key = (chat_id, message_id, self.translate_to)
        current = self._translations.get(key)
        if current is not None and current.state in ("pending", "done"):
            return
        try:
            self._check(chat_id)
        except AiUnavailable as e:
            self._set_translation(key, Transcript("error", str(e)))
            return
        self._set_translation(key, Transcript("pending"))
        self._spawn(self._translate(key))

    async def _translate(self, key: tuple[int, int, str]) -> None:
        chat_id, message_id, lang = key
        try:
            message = await self._client.send(
                {"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
            body = message_body(message.get("content", {})) or {}
            text = body.get("text", "") or (self._store.transcripts.get((chat_id, message_id))
                                            or "")
            if not text.strip():
                raise AiUnavailable("Nothing to translate")
            self._check(chat_id)
            completion = await self._complete(chat_id, "translate", self.cheap_model,
                                              assist.translate_prompt(text, lang), 0.0)
        except (AiUnavailable, OpenRouterError) as e:
            self._set_translation(key, Transcript("error", str(e)))
            return
        except TdError as e:
            self._set_translation(key, Transcript("error", f"Telegram: {e.message}"))
            return
        except Exception:
            log.exception("Translation failed")
            self._set_translation(key, Transcript("error", "Unexpected error, see the log"))
            return
        self._store.translations[key] = completion.text
        self._set_translation(key, Transcript("done", completion.text))
        await asyncio.to_thread(self._store.save_translation, *key, completion.text)

    async def translate_text(self, chat_id: int, text: str, lang: str) -> str:
        """The user's own unsent text, to show before sending. Raises AiUnavailable."""
        self._check(chat_id)
        if not text.strip():
            raise AiUnavailable("Nothing to translate")
        try:
            completion = await self._complete(chat_id, "translate", self.cheap_model,
                                              assist.translate_prompt(text, lang), 0.0)
        except OpenRouterError as e:
            raise AiUnavailable(str(e)) from e
        return completion.text

    def _set_translation(self, key: tuple[int, int, str], value: Transcript) -> None:
        self._translations[key] = value
        self._emit("translation", key[:2])

    # --- jobs: summaries, answers, digests ------------------------------------------------

    def summary(self, chat_id: int, subject: str = "") -> SummaryState:
        return self._summaries.get((chat_id, subject)) or SummaryState("")

    def summarize(self, chat_id: int, scope: str, topic_id: int = 0, topic_name: str = "",
                  last_read: int = 0) -> None:
        """Summary of the chat's recent messages (unread | day | week). In a forum topic
        (subject "topic:<id>"), only that topic; `last_read` is its read mark."""
        if scope not in summaries.SCOPES:
            raise ValueError(f"Unknown summary scope: {scope}")
        subject = f"topic:{topic_id}" if topic_id else ""
        self._start((chat_id, subject), scope, topic_name,
                    lambda: self._run_summary(chat_id, scope, topic_id, topic_name, last_read))

    def summarize_person(self, chat_id: int, sender: str, name: str) -> None:
        """Profile of one participant, built from their own messages in this chat."""
        if summaries.sender_object(sender) is None:
            raise ValueError(f"Bad sender key: {sender}")
        self._start((chat_id, sender), "person", name,
                    lambda: self._run_person(chat_id, sender, name))

    def ask(self, chat_id: int, question: str) -> None:
        """Answer a question from the chat's history (messages found by search)."""
        question = question.strip()
        if question:
            self._start((chat_id, "ask"), "ask", "", lambda: self._run_ask(chat_id, question),
                        question)

    def find_events(self, chat_id: int, scope: str = "month") -> None:
        """Agreed dates and meetings in the chat's recent history (exportable as .ics)."""
        self._start((chat_id, "events"), "events", "",
                    lambda: self._run_events(chat_id, scope))

    def collect_answers(self, chat_id: int, message_id: int, name: str = "") -> None:
        """Who answered a question (or call) in a group, how, and who didn't."""
        self._start((chat_id, f"answers:{message_id}"), "answers", name,
                    lambda: self._run_answers(chat_id, message_id))

    def ask_document(self, chat_id: int, message_id: int, question: str, name: str = "") -> None:
        question = question.strip()
        if question:
            self._start((chat_id, f"doc:{message_id}"), "doc", name,
                        lambda: self._run_document(chat_id, message_id, question), question)

    def digest(self) -> None:
        """What matters in the chats with the digest flag, since the last digest."""
        self._start((0, "digest"), "digest", "", self._run_digest)

    def promises(self) -> None:
        """What the user promised to others, across chats with AI on."""
        self._start((0, "promises"), "promises", "", self._run_promises)

    def _start(self, key: SummaryKey, scope: str, name: str,
               runner: Callable[[], Awaitable[_Result]], question: str = "") -> None:
        chat_id = key[0]
        previous = self.summary(*key)
        if previous.state == "pending":
            return
        try:
            if chat_id:
                self._check(chat_id)
            elif self._router is None:
                raise AiUnavailable("Add an OpenRouter API key in Settings to use AI features")
        except AiUnavailable as e:
            self._set_summary(key, SummaryState("error", scope, error=str(e), name=name,
                                                question=question))
            return
        self._set_summary(key, SummaryState("pending", scope, previous.text,
                                            created=previous.created, name=name,
                                            question=question or previous.question,
                                            data=previous.data))
        self._spawn(self._run(key, scope, name, question, runner))

    async def _run(self, key: SummaryKey, scope: str, name: str, question: str,
                   runner: Callable[[], Awaitable[_Result]]) -> None:
        try:
            result = await runner()
        except (AiUnavailable, OpenRouterError) as e:
            self._set_summary(key, SummaryState("error", scope, error=str(e), name=name,
                                                question=question))
            return
        except TdError as e:
            self._set_summary(key, SummaryState("error", scope, error=f"Telegram: {e.message}",
                                                name=name, question=question))
            return
        except Exception:
            log.exception("AI job %s failed", key)
            self._set_summary(key, SummaryState("error", scope, name=name, question=question,
                                                error="Unexpected error, see the log"))
            return
        state = SummaryState(
            "done", scope, result.text, created=int(time.time()), count=result.count,
            truncated=result.truncated, name=name, question=question, cost=result.cost,
            data=result.data)
        self._set_summary(key, state)
        stored = StoredSummary(scope, state.text, self.summary_model, state.created, name,
                               question, result.cost, json.dumps(result.data))
        self._store.summaries[key] = stored
        await asyncio.to_thread(self._store.save_summary, key, stored)

    async def _run_summary(self, chat_id: int, scope: str, topic_id: int = 0,
                           topic_name: str = "", last_read: int = 0) -> _Result:
        chat = self._chats.chats[chat_id]
        outside = summaries.stop_condition(
            scope, last_read if topic_id else chat.last_read_inbox_message_id)
        messages, truncated = await summaries.collect(self._client, chat_id, outside,
                                                      topic_id=topic_id)
        text, source = summaries.render(messages, self._sender_name, self._transcript_of(chat_id),
                                        reader=await self._marks_for(chat_id, messages))
        if not source.count:
            raise AiUnavailable("No unread messages" if scope == "unread"
                                else "Nothing to summarize")
        self._check(chat_id)
        reply = await self._complete(
            chat_id, "summary", self.summary_model,
            summaries.prompt(f"{chat.title} › {topic_name}" if topic_name else chat.title,
                             self._reader(with_username=True), text,
                             self.translate_to, _PERIODS[scope], source.for_you))
        text = summaries.keep_for_you(reply.text, assist.word(self.translate_to, "for_you"),
                                      source.for_you)
        return _Result(summaries.linkify(text, source), reply.cost, source.count,
                       truncated or source.truncated)

    async def _run_person(self, chat_id: int, sender: str, name: str) -> _Result:
        chat = self._chats.chats[chat_id]
        messages, truncated = await summaries.collect_from_sender(self._client, chat_id, sender)
        if not messages:
            raise AiUnavailable("No messages from this person here")
        # The reader's side of their exchanges: own messages the person replied to and own
        # replies to the person. The section about them is kept by code, not by the model.
        me = self._users.my_id or 0
        reader, replied = await self._marks_and_replied(chat_id, messages)
        theirs = {m["id"] for m in messages}
        exchange = [m for m in replied if m["id"] not in theirs]
        if me and sender != f"user:{me}":
            mine, _ = await summaries.collect_from_sender(
                self._client, chat_id, f"user:{me}", limit=OWN_FOR_PERSON)
            exchange += [m for m in mine if summaries.replied_id(m) in theirs]
        merged = {m["id"]: m for m in exchange + messages}
        text, source = summaries.render(
            [merged[i] for i in sorted(merged)], self._sender_name,
            self._transcript_of(chat_id), reader=reader, own_concerns=True)
        if not source.count:
            raise AiUnavailable("No messages from this person here")
        self._check(chat_id)
        heading = assist.word(self.translate_to, "between")
        reply = await self._complete(
            chat_id, "person", self.summary_model,
            summaries.person_prompt(chat.title, name, self._reader(), text,
                                    self.translate_to, source.for_you))
        text = summaries.keep_for_you(assist.clean_headings(reply.text), heading,
                                      source.for_you)
        return _Result(summaries.linkify(text, source), reply.cost, source.count,
                       truncated or source.truncated)

    async def _run_ask(self, chat_id: int, question: str) -> _Result:
        chat = self._chats.chats[chat_id]
        found: dict[int, dict[str, Any]] = {}
        if self.searcher is not None:
            for hit in await self.searcher(question, chat_id):
                if hit.chat_id == chat_id:
                    found[hit.message_id] = {"id": hit.message_id, "date": hit.date,
                                             "sender": hit.sender, "text": hit.text}
        recent, _ = await summaries.collect(self._client, chat_id, lambda m: False, limit=60)
        for message in recent:
            text = summaries.message_text(message, self._transcript_of(chat_id))
            if text and message["id"] not in found:
                found[message["id"]] = {"id": message["id"], "date": message.get("date", 0),
                                        "sender": self._sender_name(message), "text": text}
        if not found:
            raise AiUnavailable("Nothing in this chat to answer from")
        lines, times, senders = [], {}, {}
        for item in sorted(found.values(), key=lambda m: m["id"]):
            text = " ".join(str(item["text"]).split())
            lines.append(f"[m{item['id']}] {message_stamp(item['date'])} {item['sender']}: {text}")
            times[item["id"]] = item["date"]
            senders[item["id"]] = item["sender"]
        source = summaries.Source(times=times, count=len(lines), truncated=False,
                                  senders=senders)
        self._check(chat_id)
        reply = await self._complete(
            chat_id, "ask", self.summary_model,
            assist.ask_prompt(chat.title, self._reader(), question, "\n".join(lines),
                              self.translate_to))
        return _Result(summaries.linkify(reply.text, source), reply.cost, source.count)

    async def _run_events(self, chat_id: int, scope: str) -> _Result:
        chat = self._chats.chats[chat_id]
        since = time.time() - (7 if scope == "week" else 30) * 86400
        messages, truncated = await summaries.collect(
            self._client, chat_id, lambda m: m.get("date", 0) < since)
        text, source = self._render(chat_id, messages)
        if not source.count:
            raise AiUnavailable("No messages in this period")
        self._check(chat_id)
        reply = await self._complete(chat_id, "events", self.summary_model,
                                     assist.events_prompt(chat.title, text, self.translate_to), 0.0)
        events = [e for e in assist.parse_events(reply.text) if not e.ref or e.ref in source.times]
        markdown = summaries.linkify(assist.events_markdown(events, self.translate_to), source)
        return _Result(markdown, reply.cost, source.count, truncated,
                       {"events": [asdict(e) for e in events]})

    async def _run_answers(self, chat_id: int, message_id: int) -> _Result:
        chat = self._chats.chats[chat_id]
        question = await self._client.send(
            {"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
        after = [m for m in await self._messages_after(chat_id, question)
                 if not self._is_bot(m.get("sender_id") or {})]  # bots never answer
        question_line = summaries.message_line(question, self._sender_name(question),
                                               self._transcript_of(chat_id))
        text, source = self._render(chat_id, after)
        if not source.count:
            raise AiUnavailable("No messages after this one yet")
        addressees = await self._addressees(chat_id, question)
        self._check(chat_id)
        reply = await self._complete(chat_id, "answers", self.summary_model,
                                     assist.answers_prompt(chat.title, question_line, text,
                                                           addressees, self.translate_to))
        answer = reply.text if addressees else assist.drop_no_answer(reply.text,
                                                                     self.translate_to)
        return _Result(summaries.linkify(answer, source), reply.cost, source.count)

    async def _messages_after(self, chat_id: int, question: dict[str, Any]) -> list[Any]:
        """Messages newer than `question`, oldest first, up to a few days or pages."""
        found: dict[int, dict[str, Any]] = {}
        from_id = question["id"]
        end = question.get("date", 0) + ANSWER_WINDOW_SECONDS
        for _ in range(5):
            page = await self._client.send({
                "@type": "getChatHistory", "chat_id": chat_id, "from_message_id": from_id,
                "offset": -99, "limit": 100, "only_local": False})
            newer = [m for m in page.get("messages") or []
                     if m and m["id"] > from_id and m.get("date", 0) <= end]
            if not newer:
                break
            for message in newer:
                found[message["id"]] = message
            from_id = max(m["id"] for m in newer)
        return [found[k] for k in sorted(found)]

    async def _addressees(self, chat_id: int, question: dict[str, Any]) -> list[str]:
        """Who the question was for, by name: the people it mentions, else every member of a
        small group. Never bots or the asker. Empty when unknown: in a big group almost
        everyone is "without an answer", which says nothing."""
        asker = (question.get("sender_id") or {}).get("user_id")
        mentioned_ids, usernames = summaries.mentioned(question)
        ids = list(mentioned_ids)
        for username in sorted(usernames):
            user_id = next((u.id for u in self._users.users.values()
                            if username in {n.lower() for n in u.usernames}), 0)
            if not user_id:
                try:
                    chat = await self._client.send({"@type": "searchPublicChat",
                                                    "username": username})
                    user_id = (chat.get("type") or {}).get("user_id") or 0
                except TdError:
                    pass
            if user_id:
                ids.append(user_id)
        if not ids:
            try:
                result = await self._client.send({
                    "@type": "searchChatMembers", "chat_id": chat_id, "query": "",
                    "limit": SMALL_GROUP + 1, "filter": None})
            except TdError:
                return []
            members = result.get("members") or []
            if result.get("total_count", len(members)) > SMALL_GROUP:
                return []
            ids = [(m.get("member_id") or {}).get("user_id") or 0 for m in members]
        names = []
        for user_id in dict.fromkeys(ids):
            if user_id in (0, asker):
                continue
            user = self._users.users.get(user_id)
            if user is not None and not user.is_bot and user.full_name:
                names.append(user.full_name)
        return names

    def _is_bot(self, sender: dict[str, Any]) -> bool:
        user = self._users.users.get(sender.get("user_id") or 0)
        return sender.get("@type") == "messageSenderUser" and user is not None and user.is_bot

    async def _run_document(self, chat_id: int, message_id: int, question: str) -> _Result:
        message = await self._client.send(
            {"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
        document = (message.get("content") or {}).get("document") or {}
        file = document.get("document") or {}
        name = document.get("file_name", "")
        kind = assist.document_kind(name, document.get("mime_type", ""))
        if not file or not kind:
            raise AiUnavailable("Only PDF and text files can be asked about")
        if (file.get("size") or file.get("expected_size") or 0) > assist.MAX_DOCUMENT_BYTES:
            raise AiUnavailable("The file is too large (20 MB at most)")
        files = self._chats.files
        files.register(file)
        path = await files.fetch(file["id"], USER_PRIORITY)
        data = await asyncio.to_thread(Path(path).read_bytes)
        self._check(chat_id)
        reply = await self._complete(
            chat_id, "document", self.summary_model,
            assist.document_messages(question, name, kind, data, self.translate_to),
            plugins=assist.PDF_PLUGINS if kind == "pdf" else None)
        return _Result(reply.text, reply.cost, 1)

    async def _run_digest(self) -> _Result:
        chats = [c for c in self.enabled_chats() if self.flag(c, "digest")]
        if not chats:
            raise AiUnavailable("No chats in the digest yet: turn it on in a chat's AI menu")
        now = time.time()
        stored = float(self._store.values.get("digest_since", 0) or 0)
        since = max(stored or now - DIGEST_DEFAULT_HOURS * 3600, now - DIGEST_MAX_DAYS * 86400)
        blocks = []
        for chat_id in chats:
            messages, _ = await summaries.collect(
                self._client, chat_id, lambda m: m.get("date", 0) < since,
                limit=DIGEST_CHAT_MESSAGES)
            if messages:
                blocks.append(assist.ChatBlock(chat_id, self._chats.chats[chat_id].title,
                                               messages))
        text, source = assist.render_multi(blocks, self._sender_name, self._transcript_any)
        if not source.count:
            raise AiUnavailable("Nothing new in the digest chats")
        self._check_chats(chats)
        label = datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M")  # noqa: DTZ006
        reply = await self._complete(0, "digest", self.summary_model,
                                     assist.digest_prompt(self._reader(), text, label, self.translate_to))
        self._store.values["digest_since"] = str(int(now))
        await asyncio.to_thread(self._store.save_value, "digest_since", str(int(now)))
        return _Result(summaries.linkify(reply.text, source), reply.cost, source.count,
                       data={"since": int(since), "chats": len(blocks)})

    async def _run_promises(self) -> _Result:
        chats = self.enabled_chats()
        if not chats:
            raise AiUnavailable("Turn AI on in the chats to look through")
        me = self._users.my_id
        since = time.time() - PROMISE_DAYS * 86400
        blocks = []
        for chat_id in chats:
            messages, _ = await summaries.collect(
                self._client, chat_id, lambda m: m.get("date", 0) < since,
                limit=DIGEST_CHAT_MESSAGES)
            if any(m.get("is_outgoing") for m in messages):
                blocks.append(assist.ChatBlock(chat_id, self._chats.chats[chat_id].title,
                                               messages))
        if not blocks:
            raise AiUnavailable(f"You wrote nothing in AI chats in the last {PROMISE_DAYS} days")
        text, source = assist.render_multi(
            blocks, self._sender_name, self._transcript_any,
            mine=lambda m: bool(m.get("is_outgoing")) or (
                (m.get("sender_id") or {}).get("user_id") == me))
        self._check_chats([b.chat_id for b in blocks])
        reply = await self._complete(0, "promises", self.summary_model,
                                     assist.promises_prompt(self._reader(), text, self.translate_to))
        return _Result(summaries.linkify(reply.text, source), reply.cost, source.count,
                       data={"chats": len(blocks)})

    # --- one message: explain, suggest replies -------------------------------------------

    async def context_size(self, chat_id: int, message_id: int) -> int:
        """How many messages Explain / Suggest reply would send (shown in the menu). Collects
        the context now (TDLib only, nothing leaves the computer) and keeps it briefly."""
        if not self.is_enabled(chat_id):
            return 0
        try:
            parts = await self._context_parts(chat_id, message_id)
        except TdError:
            return 0
        return self._build_context(chat_id, parts).count

    def explain_message(self, chat_id: int, message_id: int, name: str = "") -> None:
        """Gist, what is wanted from the user, context with links, tone, open questions."""
        self._start((chat_id, f"explain:{message_id}"), "explain", name,
                    lambda: self._run_explain(chat_id, message_id, name))

    def suggest_replies(self, chat_id: int, message_id: int, name: str = "",
                        modifier: str = "") -> None:
        """Replies with different strategies, in the chat's language, translated for the
        user. modifier: "" | another | shorter | formal."""
        if modifier not in assist.MODIFIERS:
            raise ValueError(f"Unknown modifier: {modifier}")
        key = (chat_id, f"reply:{message_id}")
        already = ([o["label"] for o in self.summary(*key).data.get("options", [])]
                   if modifier == "another" else [])
        self._start(key, "reply", name,
                    lambda: self._run_replies(chat_id, message_id, name, modifier, already))

    async def _context_parts(self, chat_id: int, message_id: int) -> _ContextParts:
        cached = self._contexts.get((chat_id, message_id))
        if cached is not None and time.monotonic() - cached.created < CONTEXT_TTL:
            return cached
        target = await self._client.send(
            {"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
        chain: list[dict[str, Any]] = []
        current = target
        for _ in range(CHAIN_DEPTH):  # the reply chain is the strongest hint at what it's about
            if not (current.get("reply_to") or {}).get("message_id"):
                break
            try:
                replied = await self._client.send({"@type": "getRepliedMessage",
                                                   "chat_id": chat_id,
                                                   "message_id": current["id"]})
            except TdError:
                break
            if not replied.get("id") or replied.get("chat_id", chat_id) != chat_id or any(
                    m["id"] == replied.get("id") for m in chain):
                break
            chain.append(replied)
            current = replied
        page = await self._client.send({
            "@type": "getChatHistory", "chat_id": chat_id, "from_message_id": message_id,
            "offset": -CONTEXT_AROUND, "limit": 2 * CONTEXT_AROUND + 1, "only_local": False})
        around = [m for m in page.get("messages") or [] if m]
        if not any(m["id"] == message_id for m in around):
            around.append(target)
        parts = _ContextParts(time.monotonic(), target, chain, around)
        self._contexts[(chat_id, message_id)] = parts
        return parts

    def _build_context(self, chat_id: int, parts: _ContextParts,
                       examples: list[str] | None = None) -> assist.MessageContext:
        chat = self._chats.chats[chat_id]
        stored = self._store.summaries.get((chat_id, ""))
        return assist.message_context(
            chat.title, self._reader(), parts.target, parts.chain, parts.around,
            self._sender_name, self._transcript_of(chat_id),
            chat_summary=stored.text if stored else "", my_examples=examples)

    async def _my_examples(self, chat_id: int) -> list[str]:
        """The user's own recent messages here: their texts, so sending them is fine."""
        me = self._users.my_id
        if not me:
            return []
        try:
            found = await self._client.send({
                "@type": "searchChatMessages", "chat_id": chat_id, "topic_id": None,
                "query": "", "sender_id": {"@type": "messageSenderUser", "user_id": me},
                "from_message_id": 0, "offset": 0, "limit": 30, "filter": None})
        except TdError:
            return []
        examples = []
        for message in found.get("messages") or []:
            text = " ".join(summaries.message_text(message).split())
            text = re.sub(r"^\(reply to m\d+\) ", "", text)  # the style, not the threading
            if text:
                examples.append(text[:300])
            if len(examples) >= STYLE_EXAMPLES:
                break
        return examples

    async def _streamed(self, chat_id: int, feature: str, model: str,
                        messages: list[dict[str, Any]], cache_key: str,
                        on_text: Callable[[str], None], temperature: float = 0.3,
                        ) -> Completion:
        """A streamed request with the same limit and accounting as _complete(); the cache
        key is explicit (message, edit date, action), so a repeated click costs nothing."""
        self._check_budget(chat_id)
        hit = await asyncio.to_thread(self._store.cache_get, cache_key)
        if hit is not None:
            on_text(hit)
            return Completion(hit)
        if self._router is None:
            raise AiUnavailable("Add an OpenRouter API key in Settings to use AI features")
        completion = await self._router.stream(model, messages, on_text, temperature)
        await self._record(chat_id, feature, model, completion)
        await asyncio.to_thread(self._store.cache_put, cache_key, completion.text)
        return completion

    def _message_cache_key(self, chat_id: int, parts: _ContextParts, action: str,
                           model: str, extra: str = "") -> str:
        return (f"message:v{assist.PROMPT_VERSION}:{chat_id}:{parts.target['id']}:"
                f"{parts.target.get('edit_date', 0)}:{action}:{self.translate_to}:{model}:{extra}")

    async def _run_explain(self, chat_id: int, message_id: int, name: str) -> _Result:
        """Explains the message; a light one (a joke, emoji, agreement) gets reactions the
        chat allows and one short reply in tone instead of an analysis."""
        key = (chat_id, f"explain:{message_id}")
        parts = await self._context_parts(chat_id, message_id)
        context = self._build_context(chat_id, parts)
        allowed = await self._allowed_reactions(chat_id, message_id)
        self._check(chat_id)

        def result(text: str) -> tuple[str, dict[str, Any]]:
            explained = assist.parse_explain(text, allowed)
            return (summaries.linkify(assist.clean_headings(explained.text), context.source),
                    {"messageId": message_id, "kind": explained.kind,
                     "reactions": explained.reactions, "quickReply": explained.reply})

        def partial(text: str) -> None:
            markdown, data = result(text)
            self._set_summary(key, SummaryState("pending", "explain", markdown, name=name,
                                                data=data))

        reply = await self._streamed(
            chat_id, "explain", self.cheap_model,
            assist.explain_prompt(context, self.translate_to, allowed),
            self._message_cache_key(chat_id, parts, "explain", self.cheap_model), partial)
        text, data = result(reply.text)
        return _Result(text, reply.cost, context.count, data=data)

    async def _allowed_reactions(self, chat_id: int, message_id: int) -> list[str]:
        """Emoji reactions the user can set on the message (TDLib, nothing leaves the
        computer); Telegram's standard ones if TDLib can't tell."""
        try:
            available = await self._client.send({
                "@type": "getMessageAvailableReactions", "chat_id": chat_id,
                "message_id": message_id, "row_size": 8})
        except TdError:
            return DEFAULT_REACTIONS[:MAX_ALLOWED_REACTIONS]
        return (available_keys(available, MAX_ALLOWED_REACTIONS)
                or DEFAULT_REACTIONS[:MAX_ALLOWED_REACTIONS])

    async def _run_replies(self, chat_id: int, message_id: int, name: str, modifier: str,
                           already: list[str]) -> _Result:
        key = (chat_id, f"reply:{message_id}")
        parts = await self._context_parts(chat_id, message_id)
        context = self._build_context(chat_id, parts, await self._my_examples(chat_id))
        self._check(chat_id)

        def partial(text: str) -> None:
            analysis, options = assist.parse_reply_options(text, self.reads)
            self._set_summary(key, SummaryState(
                "pending", "reply", assist.reply_markdown(text), name=name,
                data={"analysis": analysis, "options": options, "messageId": message_id}))

        reply = await self._streamed(
            chat_id, "reply", self.summary_model,
            assist.reply_options_prompt(context, self.translate_to, modifier, already,
                                        reads=self.reads),
            self._message_cache_key(chat_id, parts, "reply", self.summary_model,
                                    ",".join(self.reads) + "|" + modifier + "|"
                                    + "|".join(already)),
            partial, temperature=0.7)
        analysis, options = assist.parse_reply_options(reply.text, self.reads)
        if not options:
            raise AiUnavailable("The model returned no usable replies, try again")
        return _Result(assist.reply_markdown(reply.text), reply.cost, context.count,
                       data={"analysis": analysis, "options": options,
                             "messageId": message_id, "modifier": modifier})

    def _set_summary(self, key: SummaryKey, value: SummaryState) -> None:
        self._summaries[key] = value
        self._emit("summary", key)

    # --- composing --------------------------------------------------------------------------

    async def suggest_reply(self, chat_id: int, tone: str, reply_to: int = 0) -> str:
        """A draft of the user's next message (never sent from here). Raises AiUnavailable."""
        self._check(chat_id)
        chat = self._chats.chats[chat_id]
        messages, _ = await summaries.collect(self._client, chat_id, lambda m: False, limit=30)
        lines = [summaries.message_line(m, "Me" if m.get("is_outgoing") else
                                        self._sender_name(m), self._transcript_of(chat_id))
                 for m in messages]
        target = ""
        if reply_to:
            try:
                original = await self._client.send(
                    {"@type": "getMessage", "chat_id": chat_id, "message_id": reply_to})
                target = summaries.message_line(original, self._sender_name(original))
            except TdError:
                pass
        if not any(lines) and not target:
            raise AiUnavailable("Nothing to reply to yet")
        self._check(chat_id)
        try:
            reply = await self._complete(
                chat_id, "reply", self.summary_model,
                assist.reply_prompt(chat.title, self._reader(), "\n".join(filter(None, lines)),
                                    tone, target), 0.7, cache=False)
        except OpenRouterError as e:
            raise AiUnavailable(str(e)) from e
        return reply.text

    async def is_relevant(self, chat_id: int, message_id: int) -> bool:
        """Smart notifications: does this new message concern the user? Errors: True."""
        if not self.flag(chat_id, "smart_notify") or self._router is None:
            return True
        try:
            page = await self._client.send({
                "@type": "getChatHistory", "chat_id": chat_id, "from_message_id": message_id,
                "offset": 0, "limit": 9, "only_local": True})
            messages = sorted((m for m in page.get("messages") or [] if m),
                              key=lambda m: m["id"])
            new = next((m for m in messages if m["id"] == message_id), None)
            if new is None:
                new = await self._client.send(
                    {"@type": "getMessage", "chat_id": chat_id, "message_id": message_id})
            context = "\n".join(summaries.message_line(m, self._sender_name(m))
                                for m in messages if m["id"] != message_id)
            chat = self._chats.chats[chat_id]
            reply = await self._complete(
                chat_id, "smart_notify", self.cheap_model,
                assist.relevance_prompt(self._reader(), chat.title, context,
                                        summaries.message_line(new, self._sender_name(new))),
                0.0)
        except (AiUnavailable, OpenRouterError, TdError) as e:
            log.info("Smart notification check failed, notifying: %s", e)
            return True
        return assist.is_yes(reply.text)

    # --- internals --------------------------------------------------------------------------

    def _check(self, chat_id: int) -> None:
        if not self.allowed(chat_id):
            raise AiUnavailable("AI is never used in secret chats")
        if not self.is_enabled(chat_id):
            raise AiUnavailable("AI is turned off for this chat")
        if self._router is None:
            raise AiUnavailable("Add an OpenRouter API key in Settings to use AI features")

    def _check_chats(self, chat_ids: list[int]) -> None:
        for chat_id in chat_ids:  # the user may have turned one off while collecting
            self._check(chat_id)

    def _render(self, chat_id: int, messages: list[Any]) -> tuple[str, summaries.Source]:
        return summaries.render(messages, self._sender_name, self._transcript_of(chat_id))

    def _transcript_of(self, chat_id: int) -> Callable[[Any], str | None]:
        return lambda m: self._store.transcripts.get((chat_id, m["id"]))

    def _transcript_any(self, message: Any) -> str | None:
        return self._store.transcripts.get((message.get("chat_id", 0), message["id"]))

    def _sender_name(self, message: dict[str, Any]) -> str:
        sender = message.get("sender_id", {})
        if sender.get("@type") == "messageSenderUser":
            user = self._users.users.get(sender.get("user_id", 0))
            return user.full_name if user else ""
        chat = self._chats.chats.get(sender.get("chat_id", 0))
        return chat.title if chat else ""

    def _reader(self, with_username: bool = False) -> str:
        me = self._users.users.get(self._users.my_id or 0)
        if me is None:
            return ""
        if with_username and me.usernames:
            return f"{me.full_name} (@{me.usernames[0]})"
        return me.full_name

    async def _marks_for(self, chat_id: int, messages: list[Any]) -> summaries.Reader:
        return (await self._marks_and_replied(chat_id, messages))[0]

    async def _marks_and_replied(self, chat_id: int, messages: list[Any]
                                 ) -> tuple[summaries.Reader, list[Any]]:
        """What marks the summary input as concerning the user: their id, @usernames and own
        messages, including older ones that messages in the slice reply to (asked from TDLib,
        nothing is sent anywhere). Also returns those older own messages."""
        me = self._users.my_id or 0
        own = {m["id"] for m in messages if summaries.is_own(m, me)}
        known = {m["id"] for m in messages}
        missing = sorted({r for m in messages if (r := summaries.replied_id(m)) and r not in known})
        replied: list[Any] = []
        if missing:
            try:
                found = await self._client.send({"@type": "getMessages", "chat_id": chat_id,
                                                 "message_ids": missing[:200]})
                replied = [m for m in found.get("messages") or [] if m and summaries.is_own(m, me)]
                own.update(m["id"] for m in replied)
            except TdError as e:
                log.info("getMessages for replied messages failed: %s", e)
        user = self._users.me
        return summaries.Reader(me, user.usernames if user else (), frozenset(own)), replied

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


def _json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text) if text else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
