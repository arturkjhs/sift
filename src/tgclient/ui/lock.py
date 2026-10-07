"""The passcode: locking the app (on request, after inactivity) and unlocking it, setting,
changing and removing it. Exposed to QML as `lock`.

At launch with a passcode, the app shows only the lock screen until the passcode decrypts the
database keys (Vault.unlock); while running, locking hides the window's content behind the
same screen and notifications lose their text (TDLib keeps running).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, QTimer, Property, Signal, Slot

from ..prefs import Prefs
from ..vault import Vault

if TYPE_CHECKING:
    from ..app import Session

log = logging.getLogger(__name__)

MIN_PASSCODE = 4
AUTO_LOCK_CHOICES = (0, 1, 5, 15, 60)  # minutes; 0 = never
_ACTIVITY = {QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
             QEvent.Type.Wheel, QEvent.Type.TouchBegin}


class LockController(QObject):
    changed = Signal()
    unlocked = Signal()

    def __init__(self, vault: Vault | None, prefs: Prefs | None = None,
                 sessions: Callable[[], list[Session]] = lambda: [],
                 parent: Any = None) -> None:
        super().__init__(parent)
        self._vault = vault
        self._prefs = prefs
        self._sessions = sessions
        self._locked = bool(vault and vault.locked)
        self._last_activity = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(15_000)
        self._timer.timeout.connect(self._check_idle)
        self._timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in _ACTIVITY:
            self._last_activity = time.monotonic()
        return False

    # --- state ------------------------------------------------------------------------------

    @Property(bool, notify=changed)
    def available(self) -> bool:
        return self._vault is not None

    @Property(bool, notify=changed)
    def hasPasscode(self) -> bool:
        return bool(self._vault and self._vault.has_passcode)

    @Property(bool, notify=changed)
    def locked(self) -> bool:
        return self._locked

    @Property(int, notify=changed)
    def autoLockMinutes(self) -> int:
        return int(self._prefs.get("auto_lock_minutes") or 0) if self._prefs else 0

    @Property("QVariantList", constant=True)
    def autoLockChoices(self) -> list[int]:
        return list(AUTO_LOCK_CHOICES)

    @Slot(int)
    def setAutoLock(self, minutes: int) -> None:
        if self._prefs is not None:
            self._prefs.set("auto_lock_minutes", int(minutes))
            self.changed.emit()

    # --- locking ----------------------------------------------------------------------------

    @Slot(str, result=bool)
    def unlock(self, passcode: str) -> bool:
        vault = self._vault
        if vault is None or not self._locked:
            return True
        ok = vault.unlock(passcode) if vault.locked else vault.check(passcode)
        if ok:
            self._locked = False
            self._last_activity = time.monotonic()
            self.changed.emit()
            self.unlocked.emit()
        return ok

    @Slot()
    def lockNow(self) -> None:
        if self.hasPasscode and not self._locked:
            self._locked = True
            self.changed.emit()

    def is_private(self) -> bool:
        """Notifications show no text while locked."""
        return self._locked

    def _check_idle(self) -> None:
        minutes = self.autoLockMinutes
        if minutes and not self._locked and self.hasPasscode and (
                time.monotonic() - self._last_activity >= minutes * 60):
            self.lockNow()

    # --- the passcode -----------------------------------------------------------------------

    @Slot(str, str, result=str)
    def setPasscode(self, passcode: str, current: str) -> str:
        """Set or change it; returns an error to show, "" when done."""
        vault = self._vault
        if vault is None:
            return "Not available"
        if vault.has_passcode and not vault.check(current):
            return "The current passcode is wrong"
        if len(passcode) < MIN_PASSCODE:
            return f"At least {MIN_PASSCODE} characters"
        vault.set_passcode(passcode)
        self._reseal()
        self.changed.emit()
        return ""

    @Slot(str, result=str)
    def removePasscode(self, current: str) -> str:
        vault = self._vault
        if vault is None or not vault.has_passcode:
            return ""
        if not vault.check(current):
            return "The passcode is wrong"
        vault.remove_passcode()
        self._reseal()
        self.changed.emit()
        return ""

    def _reseal(self) -> None:
        assert self._vault is not None
        for session in self._sessions():
            try:
                session.reseal(self._vault.data_key(session.account_key))
            except Exception:  # noqa: BLE001 - one account's key problem shouldn't stop others
                log.exception("Resealing %s failed", session.account_key)
