"""AI features for QML: the per-chat switch and flags, the AI panel of the open chat
(summaries, questions, dates, answers, documents), results across chats (digest, promises),
composer helpers (translation, reply suggestions), and settings (key, models, spending)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QStandardPaths, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFontDatabase

from ..config import remove_user_setting, save_user_setting
from ..prefs import Prefs
from ..services import assist
from ..services.ai import AiService, AiUnavailable, SummaryState
from ..services.openrouter import OpenRouter, OpenRouterError, mask_key
from ..services.summary import (link_tooltip, message_id_from_link, number_links,
                                parse_message_link)
from ..store.chats import ChatStore
from ..store.markdown import markdown_to_html
from ..store.reactions import as_items
from ..store.richtext import Palette

log = logging.getLogger(__name__)

KEY_SETTING = "OPENROUTER_API_KEY"

_SCOPE_LABELS = {"unread": "Unread", "day": "Last 24 hours", "week": "Last 7 days",
                 "events": "Last 30 days", "ask": "", "doc": "", "answers": "",
                 "digest": "", "promises": "Last 14 days", "explain": "", "reply": ""}


class AiController(QObject):
    chatChanged = Signal()
    summaryChanged = Signal()
    enabledChatsChanged = Signal()
    configChanged = Signal()
    keyStateChanged = Signal()
    keySaved = Signal()
    globalChanged = Signal()
    usageChanged = Signal()
    assistChanged = Signal()
    replySuggested = Signal(str)  # composer: put this draft into the input
    draftTranslated = Signal(str, str)  # composer: translation preview, language code
    eventsExported = Signal(str)  # path of the written .ics
    insertReply = Signal(str, "QVariant")  # composer: this text, as a reply to that message

    def __init__(
        self,
        service: AiService,
        chats: ChatStore,
        parent: Any = None,
        router_factory: Callable[[str], OpenRouter] = OpenRouter,
        save_key: Callable[[str], None] = lambda key: save_user_setting(KEY_SETTING, key),
        remove_key: Callable[[], None] = lambda: remove_user_setting(KEY_SETTING),
        prefs: Prefs | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._chats = chats
        self._router_factory = router_factory
        self._save_key = save_key
        self._remove_key = remove_key
        self._key_busy = False
        self._key_error = ""
        self._tasks: set[asyncio.Task[Any]] = set()
        self._prefs = prefs or Prefs(None)
        self._chat_id = 0
        self._subject = ""  # what the summary panel shows: "" = the chat, "user:<id>" = a person
        self._topic_id = 0  # open forum topic
        # (chat id, topic id) -> (topic name, last read message id); set by the app
        self.topic_info: Callable[[int, int], tuple[str, int]] = lambda chat, topic: ("", 0)
        self._global = "digest"  # what the digest window shows: digest | promises
        self._assist_busy = False
        self._assist_error = ""
        self._doc_names: dict[str, str] = {}  # "doc:<id>" -> file name, before the first answer
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        self._palette = Palette(mono=mono)
        service.subscribe(self._on_service)
        chats.subscribe(self._on_chats)

    # --- settings ---------------------------------------------------------------------------

    @Property(bool, notify=configChanged)
    def configured(self) -> bool:
        return self._service.configured

    @Property(str, notify=configChanged)
    def apiKeyHint(self) -> str:
        return self._service.key_hint

    @Property(str, notify=configChanged)
    def apiKeySource(self) -> str:
        """environment (shell or the dev .env: wins on the next start) | settings | ''"""
        return self._service.key_source

    @Property(bool, notify=keyStateChanged)
    def keyBusy(self) -> bool:
        return self._key_busy

    @Property(str, notify=keyStateChanged)
    def keyError(self) -> str:
        return self._key_error

    @Slot(str)
    def saveApiKey(self, key: str) -> None:
        """Check the key with OpenRouter (free), then store it and use it right away."""
        key = key.strip()
        if not key or self._key_busy:
            return
        self._set_key_state(True, "")
        self._spawn(self._save(key))

    @Slot()
    def removeApiKey(self) -> None:
        self._service.use_router(None)
        self._set_key_state(False, "")
        self._spawn(asyncio.to_thread(self._remove_key))

    async def _save(self, key: str) -> None:
        router = self._router_factory(key)
        try:
            await router.key_info()
            await asyncio.to_thread(self._save_key, key)
        except OpenRouterError as e:
            await router.aclose()
            self._set_key_state(False, str(e))
            return
        except OSError as e:
            await router.aclose()
            self._set_key_state(False, f"Could not save the key: {e.strerror or e}")
            return
        self._service.use_router(router, mask_key(key), "settings")
        self._set_key_state(False, "")
        self.keySaved.emit()

    def _set_key_state(self, busy: bool, error: str) -> None:
        if (busy, error) != (self._key_busy, self._key_error):
            self._key_busy, self._key_error = busy, error
            self.keyStateChanged.emit()

    def _spawn(self, awaitable: Any) -> None:
        task = asyncio.ensure_future(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(
            lambda t: t.cancelled() or t.exception() is None
            or log.error("API key task failed", exc_info=t.exception()))

    @Property(str, notify=configChanged)
    def summaryModel(self) -> str:
        return self._service.summary_model

    @Property(str, notify=configChanged)
    def transcriptionModel(self) -> str:
        return self._service.transcription_model

    @Property(str, notify=configChanged)
    def cheapModel(self) -> str:
        return self._service.cheap_model

    @Slot(str, str, str)
    def setModels(self, summary: str, cheap: str, transcription: str) -> None:
        self._service.set_models(summary, cheap, transcription)
        self._prefs.set("summary_model", self._service.summary_model)
        self._prefs.set("cheap_model", self._service.cheap_model)
        self._prefs.set("transcription_model", self._service.transcription_model)

    @Property(float, notify=configChanged)
    def monthlyLimit(self) -> float:
        return self._service.monthly_limit

    @Slot(float)
    def setMonthlyLimit(self, usd: float) -> None:
        self._service.set_limit(usd)
        self._prefs.set("ai_monthly_limit", self._service.monthly_limit)
        self.usageChanged.emit()

    @Property(str, notify=configChanged)
    def translateTo(self) -> str:
        return self._service.translate_to

    @Slot(str)
    def setTranslateTo(self, lang: str) -> None:
        self._service.set_translate_to(lang)
        self._prefs.set("ai_language", self._service.translate_to)

    @Property("QVariantList", notify=configChanged)
    def readLanguages(self) -> list[str]:
        """Every language the user reads, the AI language first."""
        return list(self._service.reads)

    @Slot(str, bool)
    def setReads(self, lang: str, on: bool) -> None:
        codes = [c for c in self._service.read_languages if c != lang] + ([lang] if on else [])
        self._service.set_read_languages(codes)
        self._prefs.set("read_languages", list(self._service.read_languages))

    @Property(str, notify=configChanged)
    def languageLabel(self) -> str:
        return assist.NATIVE.get(self._service.translate_to, "English")

    @Property("QVariantList", constant=True)
    def languages(self) -> list[dict[str, str]]:
        return [{"code": code, "label": assist.NATIVE[code]} for code in assist.LANGUAGES]

    @Property(str, notify=usageChanged)
    def spentTotal(self) -> str:
        return _usd(self._service.spent_total())

    @Property("QVariantList", notify=usageChanged)
    def spending(self) -> list[dict[str, Any]]:
        rows = []
        for chat_id, usd in self._service.spending():
            chat = self._chats.chats.get(chat_id)
            title = "Across chats (digest, promises)" if chat_id == 0 else (
                chat.title if chat else str(chat_id))
            rows.append({"chatId": chat_id, "title": title, "amount": _usd(usd)})
        return rows

    @Property("QVariantList", notify=enabledChatsChanged)
    def enabledChats(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for chat_id in self._service.enabled_chats():
            chat = self._chats.chats.get(chat_id)
            result.append({"chatId": chat_id, "title": chat.title if chat else str(chat_id),
                           "digest": self._service.flag(chat_id, "digest"),
                           "smart": self._service.flag(chat_id, "smart_notify"),
                           "spent": _usd(self._service.spent(chat_id))})
        result.sort(key=lambda c: c["title"].lower())
        return result

    @Slot("QVariant", bool)
    def setChatEnabled(self, chat_id: Any, enabled: bool) -> None:
        self._service.set_enabled(int(chat_id or 0), enabled)

    @Slot("QVariant")
    def forgetChat(self, chat_id: Any) -> None:
        """Turn AI off for a chat and delete its transcripts, translations and results."""
        self._service.forget(int(chat_id or 0))

    # --- the open chat ----------------------------------------------------------------------

    def _get_chat_id(self) -> int:
        return self._chat_id

    def _set_chat_id(self, chat_id: Any) -> None:
        chat_id = int(chat_id or 0)
        if chat_id != self._chat_id:
            self._chat_id = chat_id
            self._subject = ""
            self.chatChanged.emit()
            self.summaryChanged.emit()

    chatId = Property("QVariant", _get_chat_id, _set_chat_id, notify=chatChanged)

    def _get_topic_id(self) -> int:
        return self._topic_id

    def _set_topic_id(self, topic_id: Any) -> None:
        topic_id = int(topic_id or 0)
        if topic_id != self._topic_id:
            self._topic_id = topic_id
            if self._subject == "" or self._subject.startswith("topic:"):
                self._subject = f"topic:{topic_id}" if topic_id else ""
            self.summaryChanged.emit()

    # The open forum topic (bound from QML): summaries cover only it.
    topicId = Property("QVariant", _get_topic_id, _set_topic_id, notify=summaryChanged)

    def _summary_subject(self) -> str:
        return f"topic:{self._topic_id}" if self._topic_id else ""

    @Property(bool, notify=chatChanged)
    def available(self) -> bool:
        """False for secret chats: AI can't be turned on there."""
        return self._service.allowed(self._chat_id)

    @Property(bool, notify=chatChanged)
    def enabled(self) -> bool:
        return self._service.is_enabled(self._chat_id)

    @Slot(bool)
    def setEnabled(self, enabled: bool) -> None:
        self._service.set_enabled(self._chat_id, enabled)

    @Property(bool, notify=chatChanged)
    def digestEnabled(self) -> bool:
        return self._service.flag_set(self._chat_id, "digest")

    @Slot(bool)
    def setDigest(self, on: bool) -> None:
        self._service.set_flag(self._chat_id, "digest", on)

    @Property(bool, notify=chatChanged)
    def smartNotify(self) -> bool:
        return self._service.flag_set(self._chat_id, "smart_notify")

    @Slot(bool)
    def setSmartNotify(self, on: bool) -> None:
        self._service.set_flag(self._chat_id, "smart_notify", on)

    @Property(str, notify=usageChanged)
    def spentHere(self) -> str:
        return _usd(self._service.spent(self._chat_id))

    @Slot("QVariant")
    def translate(self, message_id: Any) -> None:
        if self._chat_id:
            self._service.translate(self._chat_id, int(message_id or 0))

    # --- the AI panel: questions, dates, answers, documents ---------------------------------

    @Slot(str)
    def openPanel(self, subject: str) -> None:
        """Show a subject without running anything (e.g. "ask": the question box)."""
        self._show(subject)

    @Slot(str)
    def ask(self, question: str) -> None:
        if not self._chat_id:
            return
        if self._subject.startswith("doc:"):
            message_id = int(self._subject.split(":", 1)[1])
            self._service.ask_document(self._chat_id, message_id, question, self.summaryName)
        else:
            self._show("ask")
            self._service.ask(self._chat_id, question)

    @Slot()
    def findEvents(self) -> None:
        if self._chat_id:
            self._show("events")
            self._service.find_events(self._chat_id)

    @Slot("QVariant", str)
    def collectAnswers(self, message_id: Any, name: str) -> None:
        if self._chat_id and message_id:
            self._show(f"answers:{int(message_id)}")
            self._service.collect_answers(self._chat_id, int(message_id), name)

    @Slot("QVariant", str)
    def openDocument(self, message_id: Any, name: str) -> None:
        """Ask about a file: shows the question box (the file goes out with the question)."""
        if self._chat_id and message_id:
            key = f"doc:{int(message_id)}"
            if self._service.summary(self._chat_id, key).state == "":
                self._doc_names[key] = name
            self._show(key)

    @Property(bool, notify=summaryChanged)
    def canAsk(self) -> bool:
        return self._subject == "ask" or self._subject.startswith("doc:")

    @Property(str, notify=summaryChanged)
    def summaryQuestion(self) -> str:
        return self._current().question

    @Property(str, notify=summaryChanged)
    def summaryCost(self) -> str:
        return _usd(self._current().cost) if self._current().state == "done" else ""

    @Property(bool, notify=summaryChanged)
    def hasEvents(self) -> bool:
        return bool(self._current().data.get("events"))

    @Slot()
    def exportEvents(self) -> None:
        """Write the found events as .ics and open it (Calendar offers to import it)."""
        events = [assist.Event(**e) for e in self._current().data.get("events", [])]
        if not events:
            return
        folder = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home())
        chat = self._chats.chats.get(self._chat_id)
        name = re.sub(r"[^\w\- ]+", "", chat.title if chat else "chat").strip() or "chat"
        path = Path(folder) / f"{name} - events.ics"
        try:
            path.write_text(assist.to_ics(events), encoding="utf-8")
        except OSError as e:
            log.warning("Writing %s failed: %s", path, e)
            return
        self.eventsExported.emit(str(path))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # --- one message: explain, suggest replies ------------------------------------------

    @Slot("QVariant", str)
    def explainMessage(self, message_id: Any, name: str) -> None:
        if self._chat_id and message_id:
            self._show(f"explain:{int(message_id)}")
            self._service.explain_message(self._chat_id, int(message_id), name)

    @Slot("QVariant", str)
    def suggestReplies(self, message_id: Any, name: str) -> None:
        if self._chat_id and message_id:
            self._show(f"reply:{int(message_id)}")
            self._service.suggest_replies(self._chat_id, int(message_id), name)

    @Slot(str)
    def refineReplies(self, modifier: str) -> None:
        """another | shorter | formal, for the replies shown in the panel."""
        if self._chat_id and self._subject.startswith("reply:"):
            message_id = int(self._subject.split(":", 1)[1])
            self._service.suggest_replies(self._chat_id, message_id, self.summaryName,
                                          modifier)

    @Property("QVariantList", notify=summaryChanged)
    def replyOptions(self) -> list[dict[str, str]]:
        return list(self._current().data.get("options", [])) if self._subject.startswith(
            "reply:") else []

    @Property(str, notify=summaryChanged)
    def replyAnalysis(self) -> str:
        return str(self._current().data.get("analysis", "")) if self._subject.startswith(
            "reply:") else ""

    @Property(str, notify=summaryChanged)
    def explainKind(self) -> str:
        """actionable | informational | light | "" for the message explained in the panel."""
        return str(self._current().data.get("kind", "")) if self._subject.startswith(
            "explain:") else ""

    @Property("QVariantList", notify=summaryChanged)
    def quickReactions(self) -> list[dict[str, str]]:
        """Reactions suggested for a light message: [{key, label}], only ones the chat allows."""
        if self.explainKind != "light":
            return []
        return as_items([str(k) for k in self._current().data.get("reactions", [])])

    @Property(str, notify=summaryChanged)
    def quickReply(self) -> str:
        return str(self._current().data.get("quickReply", "")) if self.explainKind == (
            "light") else ""

    @Property("QVariant", notify=summaryChanged)
    def explainedMessageId(self) -> int:
        return int(self._subject.split(":", 1)[1]) if self._subject.startswith(
            "explain:") else 0

    @Slot()
    def insertQuickReply(self) -> None:
        """The short reply to a light message into the input, as a reply. Never sends."""
        if self.quickReply and self.explainedMessageId:
            self.insertReply.emit(self.quickReply, self.explainedMessageId)

    @Slot(int)
    def insertOption(self, index: int) -> None:
        """Put a suggested reply into the input as a reply to its message. Never sends."""
        options = self.replyOptions
        if 0 <= index < len(options) and self._subject.startswith("reply:"):
            self.insertReply.emit(options[index]["text"], int(self._subject.split(":", 1)[1]))

    # --- across chats: digest and promises --------------------------------------------------

    @Slot()
    def digest(self) -> None:
        self._show_global("digest")
        self._service.digest()

    @Slot()
    def promises(self) -> None:
        self._show_global("promises")
        self._service.promises()

    @Slot(str)
    def showGlobal(self, subject: str) -> None:
        self._show_global(subject)

    def _show_global(self, subject: str) -> None:
        if subject != self._global:
            self._global = subject
            self.globalChanged.emit()

    def _global_state(self) -> SummaryState:
        return self._service.summary(0, self._global)

    @Property(str, notify=globalChanged)
    def globalSubject(self) -> str:
        return self._global

    @Property(str, notify=globalChanged)
    def globalState(self) -> str:
        return self._global_state().state

    @Property(str, notify=globalChanged)
    def globalHtml(self) -> str:
        return markdown_to_html(number_links(self._global_state().text), self._palette)

    @Property(str, notify=globalChanged)
    def globalError(self) -> str:
        return self._global_state().error

    @Property(str, notify=globalChanged)
    def globalInfo(self) -> str:
        return self._info(self._global_state())

    @Property(int, notify=enabledChatsChanged)
    def digestChats(self) -> int:
        return sum(1 for c in self._service.enabled_chats() if self._service.flag(c, "digest"))

    @Slot(str, result="QVariantList")
    def parseLink(self, link: str) -> list[Any]:
        """[chat id, message id]; chat 0 = the open chat; [0, 0] = not a message link."""
        chat_id, message_id = parse_message_link(link)
        return [chat_id, message_id]

    # --- composer helpers -------------------------------------------------------------------

    @Property(bool, notify=assistChanged)
    def assistBusy(self) -> bool:
        return self._assist_busy

    @Property(str, notify=assistChanged)
    def assistError(self) -> str:
        return self._assist_error

    @Slot(str, "QVariant")
    def suggestReply(self, tone: str, reply_to: Any = 0) -> None:
        chat_id = self._chat_id
        if chat_id and not self._assist_busy:
            self._assist(self._service.suggest_reply(chat_id, tone, int(reply_to or 0)),
                         lambda text: self.replySuggested.emit(text), chat_id)

    @Slot(str, str)
    def translateDraft(self, text: str, lang: str) -> None:
        chat_id = self._chat_id
        if chat_id and not self._assist_busy:
            self._assist(self._service.translate_text(chat_id, text, lang),
                         lambda result: self.draftTranslated.emit(result, lang), chat_id)

    @Slot()
    def clearAssistError(self) -> None:
        self._set_assist(self._assist_busy, "")

    def _assist(self, work: Any, done: Callable[[str], None], chat_id: int) -> None:
        self._set_assist(True, "")

        async def run() -> None:
            try:
                result = await work
            except AiUnavailable as e:
                self._set_assist(False, str(e))
                return
            except Exception:
                log.exception("AI assist failed")
                self._set_assist(False, "Unexpected error, see the log")
                return
            self._set_assist(False, "")
            if chat_id == self._chat_id:  # the user may have switched chats meanwhile
                done(result)

        self._spawn(run())

    def _set_assist(self, busy: bool, error: str) -> None:
        if (busy, error) != (self._assist_busy, self._assist_error):
            self._assist_busy, self._assist_error = busy, error
            self.assistChanged.emit()

    @Slot("QVariant")
    def transcribe(self, message_id: Any) -> None:
        if self._chat_id:
            self._service.transcribe(self._chat_id, int(message_id or 0))

    @Slot(str)
    def summarize(self, scope: str) -> None:
        if self._chat_id:
            self._show(self._summary_subject())
            name, last_read = (self.topic_info(self._chat_id, self._topic_id)
                               if self._topic_id else ("", 0))
            self._service.summarize(self._chat_id, scope, self._topic_id, name, last_read)

    @Slot(str, str)
    def summarizePerson(self, sender: str, name: str) -> None:
        if self._chat_id and sender:
            self._show(sender)
            self._service.summarize_person(self._chat_id, sender, name)

    @Slot()
    def summarizeAgain(self) -> None:
        summary = self._current()
        subject = self._subject
        if subject == "ask" or subject.startswith("doc:"):
            if summary.question:
                self.ask(summary.question)
        elif subject == "events":
            self.findEvents()
        elif subject.startswith("explain:"):
            self.explainMessage(int(subject.split(":", 1)[1]), summary.name)
        elif subject.startswith("reply:"):
            self.suggestReplies(int(subject.split(":", 1)[1]), summary.name)
        elif subject.startswith("answers:"):
            self.collectAnswers(int(subject.split(":", 1)[1]), summary.name)
        elif subject.startswith("topic:"):
            if summary.scope:
                self.summarize(summary.scope)
        elif subject:
            self.summarizePerson(subject, summary.name)
        elif summary.scope:
            self.summarize(summary.scope)

    @Slot()
    def showChatSummary(self) -> None:
        self._show(self._summary_subject())

    @Property(str, notify=summaryChanged)
    def subject(self) -> str:
        return self._subject

    @Property(str, notify=summaryChanged)
    def summaryName(self) -> str:
        """Who or what it is about: a person, a file, a question message."""
        return self._current().name or self._doc_names.get(self._subject, "")

    def _show(self, subject: str) -> None:
        if subject != self._subject:
            self._subject = subject
            self.summaryChanged.emit()

    def _current(self):
        return self._service.summary(self._chat_id, self._subject)

    @Property(str, notify=summaryChanged)
    def summaryState(self) -> str:
        return self._current().state

    @Property(str, notify=summaryChanged)
    def summaryScope(self) -> str:
        return self._current().scope

    @Property(str, notify=summaryChanged)
    def summaryText(self) -> str:
        return self._current().text

    @Property(str, notify=summaryChanged)
    def summaryHtml(self) -> str:
        return markdown_to_html(number_links(self._current().text), self._palette)

    def _get_link(self) -> str:
        return self._palette.link

    def _set_link(self, value: str) -> None:
        self._set_palette(link=value)

    def _get_code(self) -> str:
        return self._palette.code_background

    def _set_code(self, value: str) -> None:
        self._set_palette(code_background=value)

    # Rich text is generated here, so it needs the theme's colors (bound from QML).
    linkColor = Property(str, _get_link, _set_link, notify=summaryChanged)
    codeBackground = Property(str, _get_code, _set_code, notify=summaryChanged)

    def _set_palette(self, **changes: str) -> None:
        palette = Palette(**{**self._palette.__dict__, **changes})
        if palette != self._palette:
            self._palette = palette
            self.summaryChanged.emit()
            self.globalChanged.emit()

    @Property(str, notify=summaryChanged)
    def summaryError(self) -> str:
        return self._current().error

    @Property(str, notify=summaryChanged)
    def summaryInfo(self) -> str:
        return self._info(self._current())

    @staticmethod
    def _info(summary: SummaryState) -> str:
        parts = [_SCOPE_LABELS.get(summary.scope, "")]
        if summary.scope == "person":
            parts = ["Their messages in this chat"]
        if summary.scope == "digest" and summary.data.get("since"):
            since = datetime.fromtimestamp(summary.data["since"])  # noqa: DTZ006
            parts = [f"Since {since:%d %b %H:%M}", f"{summary.data.get('chats', 0)} chats"]
        if summary.count and summary.scope != "doc":
            more = "+" if summary.truncated else ""
            noun = "message" if summary.count == 1 and not more else "messages"
            parts.append(f"{summary.count}{more} {noun}")
        if summary.created:
            parts.append(datetime.fromtimestamp(summary.created).strftime("%d %b %H:%M"))  # noqa: DTZ006
        if summary.state == "done" and summary.cost:
            parts.append(_usd(summary.cost))
        return " · ".join(p for p in parts if p)

    @Slot(str, result=str)
    def linkTooltip(self, link: str) -> str:
        """"Sender, 02.10 19:56" for a citation chip; "" for other links."""
        return link_tooltip(link)

    @Slot(str, result="QVariant")
    def messageIdFromLink(self, link: str) -> int:
        return message_id_from_link(link)

    # --- internals --------------------------------------------------------------------------

    def _on_service(self, kind: str, payload: Any) -> None:
        if kind == "config":
            self.configChanged.emit()
        elif kind in ("enabled", "flags"):
            self.enabledChatsChanged.emit()
            if payload == self._chat_id:
                self.chatChanged.emit()
        elif kind == "summary" and payload == (self._chat_id, self._subject):
            self.summaryChanged.emit()
        elif kind == "summary" and payload == (0, self._global):
            self.globalChanged.emit()
        elif kind == "usage":
            self.usageChanged.emit()

    def _on_chats(self, kind: str, payload: Any) -> None:
        if kind == "chat" and self._service.is_enabled(payload):
            self.enabledChatsChanged.emit()  # title change
        if kind == "chat" and payload == self._chat_id:
            self.chatChanged.emit()  # the chat may have just arrived


def _usd(amount: float) -> str:
    if amount <= 0:
        return "$0"
    return f"${amount:.4f}" if amount < 0.01 else f"${amount:.2f}"
