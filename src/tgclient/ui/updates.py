"""Update checks for QML: state, the found release, download and install (services/updates.py).

Checks once shortly after start and then daily, if the user leaves automatic checks on and the
build knows its repository.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Property,
    QObject,
    QProcess,
    QStandardPaths,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices

from ..prefs import Prefs
from ..services.updates import Release, UpdateChecker, UpdateError, replace_appimage

log = logging.getLogger(__name__)

FIRST_CHECK_MS = 30_000
CHECK_EVERY_MS = 24 * 3600 * 1000


class UpdateController(QObject):
    changed = Signal()
    quitRequested = Signal()  # restart after installing: the app closes TDLib, then exits

    def __init__(self, checker: UpdateChecker | None, prefs: Prefs, version: str,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self._checker = checker
        self._prefs = prefs
        self._version = version
        self._state = "idle"  # idle | checking | upToDate | available | downloading | ready | error
        self._release: Release | None = None
        self._progress = 0.0
        self._error = ""
        self._downloaded: Path | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_check)
        self._task: asyncio.Task[Any] | None = None

    def start(self) -> None:
        if self._checker is not None:
            QTimer.singleShot(FIRST_CHECK_MS, self._auto_check)
            self._timer.start(CHECK_EVERY_MS)

    @Property(bool, constant=True)
    def supported(self) -> bool:
        return self._checker is not None

    @Property(str, constant=True)
    def version(self) -> str:
        return self._version

    @Property(bool, notify=changed)
    def automatic(self) -> bool:
        return bool(self._prefs.get("auto_update"))

    @Slot(bool)
    def setAutomatic(self, value: bool) -> None:
        self._prefs.set("auto_update", value)
        self.changed.emit()

    @Property(str, notify=changed)
    def state(self) -> str:
        return self._state

    @Property(str, notify=changed)
    def newVersion(self) -> str:
        return self._release.version if self._release else ""

    @Property(str, notify=changed)
    def releaseUrl(self) -> str:
        return self._release.url if self._release else ""

    @Property(bool, notify=changed)
    def canInstall(self) -> bool:
        return bool(self._release and self._release.asset_url and self._checker
                    and self._checker.kind in ("appimage", "dmg"))

    @Property(float, notify=changed)
    def progress(self) -> float:
        return self._progress

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Slot()
    def check(self) -> None:
        if self._checker is not None and self._state not in ("checking", "downloading"):
            self._run(self._check())

    @Slot()
    def install(self) -> None:
        """Download the release's file and put it in place (or open it, on macOS)."""
        if not self.canInstall or self._state == "downloading":
            if self._release:
                QDesktopServices.openUrl(QUrl(self._release.url))
            return
        self._run(self._install())

    @Slot()
    def restart(self) -> None:
        appimage = os.environ.get("APPIMAGE")
        if self._state == "ready" and appimage:
            QProcess.startDetached(appimage, sys.argv[1:])
            self.quitRequested.emit()

    @Slot()
    def openPage(self) -> None:
        if self._release:
            QDesktopServices.openUrl(QUrl(self._release.url))

    # --- internals --------------------------------------------------------------------------

    def _auto_check(self) -> None:
        if self.automatic and self._state in ("idle", "upToDate", "error"):
            self.check()

    async def _check(self) -> None:
        assert self._checker is not None
        self._set("checking")
        try:
            self._release = await self._checker.latest()
        except UpdateError as e:
            self._set("error", str(e))
            return
        self._set("available" if self._release else "upToDate")

    async def _install(self) -> None:
        assert self._checker is not None and self._release is not None
        folder = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation) or Path.home())
        self._progress = 0.0
        self._set("downloading")
        try:
            path = await self._checker.download(self._release, folder, self._on_progress)
            if self._checker.kind == "appimage":
                await asyncio.to_thread(replace_appimage, path,
                                        Path(os.environ["APPIMAGE"]))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except (UpdateError, OSError) as e:
            self._set("error", str(e))
            return
        self._downloaded = path
        self._set("ready")

    def _on_progress(self, fraction: float) -> None:
        self._progress = fraction
        self.changed.emit()

    def _set(self, state: str, error: str = "") -> None:
        self._state, self._error = state, error
        self.changed.emit()

    def _run(self, coro: Any) -> None:
        self._task = asyncio.ensure_future(coro)
        self._task.add_done_callback(
            lambda t: t.cancelled() or t.exception() is None
            or log.error("Update task failed", exc_info=t.exception()))
