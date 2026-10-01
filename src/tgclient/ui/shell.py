"""App-level actions exposed to QML."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot


class ShellController(QObject):
    themeChanged = Signal()

    def __init__(self, quit_event: asyncio.Event, parent: Any = None) -> None:
        super().__init__(parent)
        self._quit_event = quit_event
        self._theme = os.environ.get("TGC_THEME", "system")

    @Property(str, notify=themeChanged)
    def theme(self) -> str:
        """system | light | dark"""
        return self._theme

    @Slot(str)
    def setTheme(self, theme: str) -> None:
        if theme != self._theme:
            self._theme = theme
            self.themeChanged.emit()

    @Slot()
    def requestQuit(self) -> None:
        """Window was closed: let Python close TDLib cleanly, then the app exits."""
        self._quit_event.set()
