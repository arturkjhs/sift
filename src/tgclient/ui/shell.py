"""App-level actions exposed to QML."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QCoreApplication, QLocale, QObject, QTranslator, Signal, Slot

from ..prefs import Prefs

I18N = Path(__file__).resolve().parent / "i18n"
UI_LANGUAGES = ("en", "ru", "uk", "cs")  # English is the source; the others have .qm files


def system_ui_language() -> str:
    """The first of the system's UI languages we have, else English."""
    for name in QLocale.system().uiLanguages():
        code = name.replace("_", "-").split("-")[0].lower()
        if code in UI_LANGUAGES:
            return code
    return "en"


class ShellController(QObject):
    themeChanged = Signal()
    languageChanged = Signal()

    def __init__(
        self, quit_event: asyncio.Event, prefs: Prefs | None = None, parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._quit_event = quit_event
        self._prefs = prefs
        saved = prefs.get("theme") if prefs else "system"
        self._theme = os.environ.get("TGC_THEME") or saved or "system"
        self._language = (os.environ.get("TGC_LANGUAGE")
                          or (prefs.get("ui_language") if prefs else "") or "")
        self._translator: QTranslator | None = None
        self.engines: list[Any] = []  # QML engines to retranslate when the language changes
        self._install_translator()

    @Property(str, notify=themeChanged)
    def theme(self) -> str:
        """system | light | dark"""
        return self._theme

    @Slot(str)
    def setTheme(self, theme: str) -> None:
        if theme != self._theme:
            self._theme = theme
            if self._prefs is not None:
                self._prefs.set("theme", theme)
            self.themeChanged.emit()

    @Property(str, notify=languageChanged)
    def language(self) -> str:
        """The UI language setting: "" follows the system, else en | ru | uk | cs."""
        return self._language

    @Slot(str)
    def setLanguage(self, language: str) -> None:
        if language == self._language or (language and language not in UI_LANGUAGES):
            return
        self._language = language
        if self._prefs is not None:
            self._prefs.set("ui_language", language)
        self._install_translator()
        for engine in self.engines:
            engine.retranslate()
        self.languageChanged.emit()

    def effective_language(self) -> str:
        return self._language or system_ui_language()

    def _install_translator(self) -> None:
        app = QCoreApplication.instance()
        if app is None:
            return
        if self._translator is not None:
            app.removeTranslator(self._translator)
            self._translator = None
        language = self.effective_language()
        if language == "en":
            return
        translator = QTranslator(self)
        if translator.load(str(I18N / f"tgclient_{language}.qm")):
            app.installTranslator(translator)
            self._translator = translator

    @Slot()
    def requestQuit(self) -> None:
        """Window was closed: let Python close TDLib cleanly, then the app exits."""
        self._quit_event.set()
