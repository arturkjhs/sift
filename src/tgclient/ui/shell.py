"""App-level actions exposed to QML."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..prefs import Prefs


class ShellController(QObject):
    themeChanged = Signal()

    def __init__(
        self, quit_event: asyncio.Event, prefs: Prefs | None = None, parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._quit_event = quit_event
        self._prefs = prefs
        saved = prefs.get("theme") if prefs else "system"
        self._theme = os.environ.get("TGC_THEME") or saved or "system"

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

    @Slot()
    def requestQuit(self) -> None:
        """Window was closed: let Python close TDLib cleanly, then the app exits."""
        self._quit_event.set()
