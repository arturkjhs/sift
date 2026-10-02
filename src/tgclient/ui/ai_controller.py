"""AI features for QML: the per-chat switch, summaries of the open chat, settings info."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QFontDatabase

from ..config import remove_user_setting, save_user_setting
from ..services.ai import AiService
from ..services.openrouter import OpenRouter, OpenRouterError, mask_key
from ..services.summary import message_id_from_link
from ..store.chats import ChatStore
from ..store.markdown import markdown_to_html
from ..store.richtext import Palette

log = logging.getLogger(__name__)

KEY_SETTING = "OPENROUTER_API_KEY"

_SCOPE_LABELS = {"unread": "Unread", "day": "Last 24 hours", "week": "Last 7 days"}


class AiController(QObject):
    chatChanged = Signal()
    summaryChanged = Signal()
    enabledChatsChanged = Signal()
    configChanged = Signal()
    keyStateChanged = Signal()
    keySaved = Signal()

    def __init__(
        self,
        service: AiService,
        chats: ChatStore,
        parent: Any = None,
        router_factory: Callable[[str], OpenRouter] = OpenRouter,
        save_key: Callable[[str], None] = lambda key: save_user_setting(KEY_SETTING, key),
        remove_key: Callable[[], None] = lambda: remove_user_setting(KEY_SETTING),
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
        self._chat_id = 0
        self._subject = ""  # what the summary panel shows: "" = the chat, "user:<id>" = a person
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

    @Property(str, constant=True)
    def summaryModel(self) -> str:
        return self._service.summary_model

    @Property(str, constant=True)
    def transcriptionModel(self) -> str:
        return self._service.transcription_model

    @Property("QVariantList", notify=enabledChatsChanged)
    def enabledChats(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for chat_id in self._service.enabled_chats():
            chat = self._chats.chats.get(chat_id)
            result.append({"chatId": chat_id, "title": chat.title if chat else str(chat_id)})
        result.sort(key=lambda c: c["title"].lower())
        return result

    @Slot("QVariant", bool)
    def setChatEnabled(self, chat_id: Any, enabled: bool) -> None:
        self._service.set_enabled(int(chat_id or 0), enabled)

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

    @Slot("QVariant")
    def transcribe(self, message_id: Any) -> None:
        if self._chat_id:
            self._service.transcribe(self._chat_id, int(message_id or 0))

    @Slot(str)
    def summarize(self, scope: str) -> None:
        if self._chat_id:
            self._show("")
            self._service.summarize(self._chat_id, scope)

    @Slot(str, str)
    def summarizePerson(self, sender: str, name: str) -> None:
        if self._chat_id and sender:
            self._show(sender)
            self._service.summarize_person(self._chat_id, sender, name)

    @Slot()
    def summarizeAgain(self) -> None:
        summary = self._current()
        if self._subject:
            self.summarizePerson(self._subject, summary.name)
        elif summary.scope:
            self.summarize(summary.scope)

    @Slot()
    def showChatSummary(self) -> None:
        self._show("")

    @Property(str, notify=summaryChanged)
    def subject(self) -> str:
        return self._subject

    @Property(str, notify=summaryChanged)
    def summaryName(self) -> str:
        """Person summaries: who it is about."""
        return self._current().name

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
        return markdown_to_html(self._current().text, self._palette)

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

    @Property(str, notify=summaryChanged)
    def summaryError(self) -> str:
        return self._current().error

    @Property(str, notify=summaryChanged)
    def summaryInfo(self) -> str:
        summary = self._current()
        parts = [_SCOPE_LABELS.get(summary.scope, "")]
        if summary.scope == "person":
            parts = ["Their messages in this chat"]
        if summary.count:
            more = "+" if summary.truncated else ""
            parts.append(f"{summary.count}{more} messages")
        if summary.created:
            parts.append(datetime.fromtimestamp(summary.created).strftime("%d %b %H:%M"))  # noqa: DTZ006
        return " · ".join(p for p in parts if p)

    @Slot(str, result="QVariant")
    def messageIdFromLink(self, link: str) -> int:
        return message_id_from_link(link)

    # --- internals --------------------------------------------------------------------------

    def _on_service(self, kind: str, payload: Any) -> None:
        if kind == "config":
            self.configChanged.emit()
        elif kind == "enabled":
            self.enabledChatsChanged.emit()
            if payload == self._chat_id:
                self.chatChanged.emit()
        elif kind == "summary" and payload == (self._chat_id, self._subject):
            self.summaryChanged.emit()

    def _on_chats(self, kind: str, payload: Any) -> None:
        if kind == "chat" and self._service.is_enabled(payload):
            self.enabledChatsChanged.emit()  # title change
        if kind == "chat" and payload == self._chat_id:
            self.chatChanged.emit()  # the chat may have just arrived
