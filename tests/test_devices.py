"""Settings → Devices: the active sessions, ending one or all others."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import FakeLib, error, ok, qt_app, wait_until

from tgclient.td import TdHub

SESSIONS = [
    {"id": "11", "is_current": False, "application_name": "Telegram Android",
     "application_version": "11.2", "device_model": "Pixel 8", "platform": "Android",
     "system_version": "15", "last_active_date": 1_700_000_000, "ip_address": "1.2.3.4",
     "location": "Prague, Czechia"},
    {"id": "10", "is_current": True, "application_name": "tgclient",
     "application_version": "0.1.0", "device_model": "Darwin arm64", "platform": "",
     "system_version": "macOS", "last_active_date": 1_700_000_500, "ip_address": "",
     "location": ""},
]


class DevicesTest(unittest.IsolatedAsyncioTestCase):
    async def test_list_and_terminate(self) -> None:
        qt_app()
        from tgclient.ui.devices import DevicesController

        sessions = list(SESSIONS)

        def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
            match req["@type"]:
                case "getActiveSessions":
                    return [{"@type": "sessions", "sessions": list(sessions),
                             "inactive_session_ttl_days": 180, "@extra": req["@extra"]}]
                case "terminateSession":
                    sessions[:] = [s for s in sessions if s["id"] != req["session_id"]]
                case "terminateAllOtherSessions":
                    return [error(req, 406, "FRESH_RESET_AUTHORISATION_FORBIDDEN")]
            return [ok(req)]

        lib = FakeLib(responder)
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        devices = DevicesController(hub.create_client())
        devices.refresh()
        await wait_until(lambda: len(devices.sessions) == 2)
        first, second = devices.sessions
        self.assertEqual((first["title"], first["current"], first["active"]),
                         ("Darwin arm64", True, "online"))
        self.assertEqual(second["details"], "Telegram Android 11.2 · Android 15")
        self.assertEqual(second["place"], "Prague, Czechia · 1.2.3.4")
        devices.terminateOthers()
        await wait_until(lambda: devices.error != "")
        self.assertIn("FRESH_RESET", devices.error)
        devices.terminate("11")
        await wait_until(lambda: len(devices.sessions) == 1)
        self.assertEqual(devices.error, "")


if __name__ == "__main__":
    unittest.main()
