"""Settings: privacy rules (exceptions kept), notifications by chat type, storage."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import FakeLib, ok, qt_app, wait_until

from tgclient.td import TdHub
from tgclient.ui.privacy import main_rule, with_main_rule

ALLOW_OLENA = {"@type": "userPrivacySettingRuleAllowUsers", "user_ids": [5]}


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    extra = req.get("@extra")
    match req["@type"]:
        case "getUserPrivacySettingRules":
            rules = ([ALLOW_OLENA, {"@type": "userPrivacySettingRuleAllowContacts"},
                      {"@type": "userPrivacySettingRuleRestrictAll"}]
                     if req["setting"]["@type"] == "userPrivacySettingShowStatus"
                     else [{"@type": "userPrivacySettingRuleAllowAll"}])
            return [{"@type": "userPrivacySettingRules", "rules": rules, "@extra": extra}]
        case "getScopeNotificationSettings":
            muted = req["scope"]["@type"] == "notificationSettingsScopeChannelChats"
            return [{"@type": "scopeNotificationSettings", "mute_for": 999 if muted else 0,
                     "sound_id": "0", "show_preview": True, "@extra": extra}]
        case "getStorageStatisticsFast":
            return [{"@type": "storageStatisticsFast", "files_size": 5 * 1024 * 1024,
                     "file_count": 12, "database_size": 2048, "@extra": extra}]
    return [ok(req)]


class PrivacyTest(unittest.IsolatedAsyncioTestCase):
    def test_rules(self) -> None:
        self.assertEqual(main_rule([ALLOW_OLENA, {"@type": "userPrivacySettingRuleRestrictAll"}]),
                         "nobody")
        rules = with_main_rule([ALLOW_OLENA, {"@type": "userPrivacySettingRuleAllowAll"}],
                               "contacts")
        self.assertEqual([r["@type"] for r in rules],
                         ["userPrivacySettingRuleAllowUsers",
                          "userPrivacySettingRuleAllowContacts",
                          "userPrivacySettingRuleRestrictAll"])
        self.assertEqual(main_rule(rules), "contacts")

    async def test_controller(self) -> None:
        qt_app()
        from tgclient.ui.privacy import PrivacyController

        lib = FakeLib(responder)
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        privacy = PrivacyController(hub.create_client())
        privacy.load()
        await wait_until(lambda: privacy.storage.get("fileCount") == 12)
        self.assertEqual((privacy.choices["lastSeen"], privacy.choices["photo"]),
                         ("contacts", "everybody"))
        self.assertEqual(privacy.scopes, {"private": True, "groups": True, "channels": False})
        self.assertEqual(privacy.storage["files"], "5.0 MB")
        privacy.choose("lastSeen", "nobody")
        privacy.setScope("channels", True)
        privacy.clearCache()
        await wait_until(lambda: any(r["@type"] == "optimizeStorage" for r in lib.sent))
        sent = next(r for r in lib.sent if r["@type"] == "setUserPrivacySettingRules")
        self.assertEqual(sent["rules"]["rules"],
                         [ALLOW_OLENA, {"@type": "userPrivacySettingRuleRestrictAll"}])
        scope = next(r for r in lib.sent if r["@type"] == "setScopeNotificationSettings")
        self.assertEqual(scope["notification_settings"]["mute_for"], 0)
        await wait_until(lambda: privacy.choices["lastSeen"] == "nobody")


if __name__ == "__main__":
    unittest.main()
