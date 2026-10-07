"""Where the account is logged in (Settings → Devices): the active sessions, ending one or
all others. Exposed to QML as `devices`."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..store.format import short_time
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)


class DevicesController(QObject):
    changed = Signal()

    def __init__(self, client: TdClient, parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._rows: list[dict[str, Any]] = []
        self._busy = False
        self._error = ""
        self._tasks: set[asyncio.Task[Any]] = set()

    @Property("QVariantList", notify=changed)
    def sessions(self) -> list[dict[str, Any]]:
        """{id, title, details, place, active, current} — this device first."""
        return list(self._rows)

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Slot()
    def refresh(self) -> None:
        self._spawn(self._load())

    @Slot(str)
    def terminate(self, session_id: str) -> None:
        self._spawn(self._run({"@type": "terminateSession", "session_id": session_id}))

    @Slot()
    def terminateOthers(self) -> None:
        self._spawn(self._run({"@type": "terminateAllOtherSessions"}))

    async def _run(self, request: dict[str, Any]) -> None:
        try:
            await self._client.send(request)
        except TdError as e:
            # A fresh session can't end others for the first day (FRESH_RESET_AUTHORISATION…).
            log.warning("%s failed: %s", request["@type"], e)
            self._error = e.message
            self.changed.emit()
            return
        await self._load()

    async def _load(self) -> None:
        self._busy = True
        self.changed.emit()
        try:
            found = await self._client.send({"@type": "getActiveSessions"})
            self._error = ""
        except TdError as e:
            log.info("getActiveSessions failed: %s", e)
            found = {}
            self._error = e.message
        self._busy = False
        rows = [_row(s) for s in found.get("sessions") or []]
        rows.sort(key=lambda r: (not r["current"], -r["lastActive"]))
        self._rows = rows
        self.changed.emit()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _row(session: dict[str, Any]) -> dict[str, Any]:
    app = " ".join(p for p in (session.get("application_name", ""),
                               session.get("application_version", "")) if p)
    device = session.get("device_model", "")
    system = " ".join(p for p in (session.get("platform", ""), session.get("system_version", ""))
                      if p)
    last = int(session.get("last_active_date") or 0)
    return {
        "id": str(session.get("id", "")),  # int64: a string in JSON, keep it one
        "title": device or app or "Unknown device",
        "details": " · ".join(p for p in (app, system) if p),
        "place": " · ".join(p for p in (session.get("location", ""),
                                        session.get("ip_address", "")) if p),
        "active": "online" if session.get("is_current") else (short_time(last) if last else ""),
        "lastActive": last,
        "current": bool(session.get("is_current")),
        "unconfirmed": bool(session.get("is_unconfirmed")),
    }
