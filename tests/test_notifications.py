"""Notifier (TDLib notification groups -> system notifications), the controller and badge."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fakes import FakeLib, new_chat, qt_app, wait_until

from tgclient.prefs import Prefs
from tgclient.store.chats import ChatStore
from tgclient.store.notifications import GROUP_COUNT_MAX, Notice
from tgclient.store.users import UserStore
from tgclient.td import TdHub


def message(chat_id: int, mid: int, text: str, user: int = 5) -> dict[str, Any]:
    return {"@type": "message", "id": mid, "chat_id": chat_id, "date": 1_700_000_000,
            "sender_id": {"@type": "messageSenderUser", "user_id": user},
            "content": {"@type": "messageText", "text": {"text": text, "entities": []}}}


def group_update(group: int, chat_id: int, added: list[dict[str, Any]],
                 removed: list[int] | None = None) -> dict[str, Any]:
    return {"@type": "updateNotificationGroup", "notification_group_id": group,
            "type": {"@type": "notificationGroupTypeMessages"}, "chat_id": chat_id,
            "notification_settings_chat_id": chat_id, "notification_sound_id": "0",
            "total_count": len(added), "added_notifications": added,
            "removed_notification_ids": removed or []}


def notification(nid: int, msg: dict[str, Any], show_preview: bool = True,
                 silent: bool = False) -> dict[str, Any]:
    return {"@type": "notification", "id": nid, "date": 1_700_000_000, "is_silent": silent,
            "type": {"@type": "notificationTypeNewMessage", "message": msg,
                     "show_preview": show_preview}}


class NotifierTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        qt_app()
        from tgclient.ui.notifications import NotificationController, NullBackend

        self.lib = FakeLib()
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.backend = NullBackend()
        self.open_chat = 0
        prefs_dir = tempfile.mkdtemp()
        self.prefs = Prefs(Path(prefs_dir) / "prefs.json")
        self.controller = NotificationController(
            self.client, self.chats, self.users, self.prefs, self.backend,
            open_chat=lambda: self.open_chat)
        await self.push(
            new_chat(1, "Olena", 10),
            new_chat(2, "Prague IT", 20, "chatTypeSupergroup"),
            new_chat(3, "Secret", 30, "chatTypeSecret"),
            {"@type": "updateUser", "user": {"id": 5, "first_name": "Petr", "last_name": "N"}},
        )

    async def push(self, *events: dict[str, Any]) -> None:
        seen: list[Any] = []
        off = self.client.on("updateTestMarker", seen.append)
        for event in (*events, {"@type": "updateTestMarker"}):
            self.lib.push(event)
        await wait_until(lambda: bool(seen))
        off()

    async def test_start_enables_tdlib_notifications(self) -> None:
        await self.controller.start()
        option = next(r for r in self.lib.sent if r["@type"] == "setOption")
        self.assertEqual(option["name"], "notification_group_count_max")
        self.assertEqual(option["value"]["value"], GROUP_COUNT_MAX)

    async def test_show_and_withdraw(self) -> None:
        await self.push(group_update(7, 2, [notification(1, message(2, 100, "deploy  is\ndone"))]))
        self.assertEqual(self.backend.shown, [Notice(
            key="7:1", chat_id=2, message_id=100, title="Prague IT", subtitle="Petr N",
            body="deploy is done", silent=False)])
        await self.push(group_update(7, 2, [], removed=[1, 99]))
        self.assertEqual(self.backend.withdrawn, ["7:1"])  # 99 was never shown

    async def test_click_requests_the_chat(self) -> None:
        requested: list[Any] = []
        self.controller.chatRequested.connect(requested.append)
        await self.push(group_update(7, 1, [notification(3, message(1, 5, "hi"))]))
        self.backend.on_activated("7:3")
        self.assertEqual(requested, [1])

    async def test_preview_privacy(self) -> None:
        await self.push(
            group_update(1, 1, [notification(1, message(1, 1, "a"), show_preview=False)]),
            group_update(2, 3, [notification(2, message(3, 2, "secret text"))]),
        )
        self.assertEqual([n.body for n in self.backend.shown], ["New message", "New message"])
        self.controller.setShowPreview(False)
        await self.push(group_update(3, 2, [notification(3, message(2, 3, "text"))]))
        self.assertEqual((self.backend.shown[-1].body, self.backend.shown[-1].subtitle),
                         ("New message", ""))
        self.assertFalse(Prefs(self.prefs._path).get("notification_preview"))  # persisted

    async def test_sound_setting_and_locked_privacy(self) -> None:
        self.controller.setSound(False)
        await self.push(group_update(1, 1, [notification(1, message(1, 1, "a"))]))
        self.assertTrue(self.backend.shown[-1].silent)
        self.assertFalse(Prefs(self.prefs._path).get("notification_sound"))
        self.controller.setSound(True)
        self.controller.notifier.private = lambda: True  # the app is locked
        await self.push(group_update(2, 1, [notification(2, message(1, 2, "secret"))]))
        self.assertEqual((self.backend.shown[-1].silent, self.backend.shown[-1].body),
                         (False, "New message"))

    async def test_disabled_and_suppressed(self) -> None:
        self.controller.setEnabled(False)
        await self.push(group_update(1, 1, [notification(1, message(1, 1, "a"))]))
        self.assertEqual(self.backend.shown, [])
        self.controller.setEnabled(True)
        self.open_chat = 1  # open, but the offscreen app isn't "active": still shown
        await self.push(group_update(1, 1, [notification(2, message(1, 2, "b"))]))
        self.assertEqual(len(self.backend.shown), 1)
        self.controller.notifier.suppress = lambda chat_id: chat_id == 1
        await self.push(group_update(1, 1, [notification(3, message(1, 3, "c"))]))
        self.assertEqual(len(self.backend.shown), 1)

    async def test_bursts_are_limited(self) -> None:
        from tgclient.store.notifications import BURST_LIMIT

        # one update with several messages of a chat: only the newest pops up
        await self.push(group_update(1, 1, [notification(i, message(1, i, f"m{i}"))
                                            for i in (1, 2, 3)]))
        self.assertEqual([n.body for n in self.backend.shown], ["m3"])
        # many chats at once (e.g. after being offline): capped
        await self.push(*(group_update(10 + i, 2, [notification(1, message(2, i, "x"))])
                          for i in range(10)))
        self.assertEqual(len(self.backend.shown), BURST_LIMIT)

    async def test_badge_follows_unread_unmuted_messages(self) -> None:
        await self.push({"@type": "updateUnreadMessageCount", "chat_list": {
            "@type": "chatListMain"}, "unread_count": 9, "unread_unmuted_count": 4})
        self.assertEqual(self.controller._badge, 4)
        await self.push({"@type": "updateUnreadMessageCount", "chat_list": {
            "@type": "chatListArchive"}, "unread_count": 50, "unread_unmuted_count": 50})
        self.assertEqual(self.controller._badge, 4)  # archive doesn't count


class DBusMessageTest(unittest.TestCase):
    def test_notify_call(self) -> None:
        try:
            import jeepney
        except ImportError:
            self.skipTest("jeepney is Linux-only")
        from tgclient.ui.notifications import DBusBackend

        notice = Notice(key="1:1", chat_id=1, message_id=1, title="Team", subtitle="Ann",
                        body="a < b & c", silent=True)
        msg = DBusBackend().notify_message(notice)
        self.assertEqual(msg.header.fields[jeepney.HeaderFields.signature], "susssasa{sv}i")
        self.assertEqual(msg.body[3], "Team")
        self.assertEqual(msg.body[4], "Ann: a &lt; b &amp; c")
        self.assertEqual(msg.body[6]["suppress-sound"], ("b", True))


class PrefsTest(unittest.TestCase):
    def test_defaults_roundtrip_and_broken_file(self) -> None:
        path = Path(tempfile.mkdtemp()) / "prefs.json"
        prefs = Prefs(path)
        self.assertTrue(prefs.get("notifications"))
        prefs.set("theme", "dark")
        self.assertEqual(Prefs(path).get("theme"), "dark")
        path.write_text("{broken")
        self.assertEqual(Prefs(path).get("theme"), "system")
        os.remove(path)


if __name__ == "__main__":
    unittest.main()
