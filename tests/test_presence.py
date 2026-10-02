"""PresenceStore (statuses, typing, member counts) and its text helpers."""

from __future__ import annotations

import time
import unittest
from datetime import datetime
from typing import Any

from fakes import FakeLib, wait_until

from tgclient.store.presence import (
    PresenceStore,
    Typing,
    members_text,
    status_text,
    typing_text,
)
from tgclient.td import TdHub


def local(*args: int) -> datetime:
    return datetime(*args)  # noqa: DTZ001 - statuses are shown in local time by design


NOW = local(2026, 3, 10, 15, 0)


def offline(when: datetime) -> dict[str, Any]:
    return {"@type": "userStatusOffline", "was_online": int(when.timestamp())}


class TextTest(unittest.TestCase):
    def test_status_text(self) -> None:
        online = {"@type": "userStatusOnline", "expires": int(NOW.timestamp()) + 60}
        self.assertEqual(status_text(online, NOW), "online")
        expired = {"@type": "userStatusOnline", "expires": int(NOW.timestamp()) - 300}
        self.assertEqual(status_text(expired, NOW), "last seen 5 minutes ago")
        self.assertEqual(status_text(offline(local(2026, 3, 10, 14, 59, 30)), NOW),
                         "last seen just now")
        self.assertEqual(status_text(offline(local(2026, 3, 10, 9, 5)), NOW),
                         "last seen at 09:05")
        self.assertEqual(status_text(offline(local(2026, 3, 9, 21, 40)), NOW),
                         "last seen yesterday at 21:40")
        self.assertEqual(status_text(offline(local(2026, 1, 2, 8, 0)), NOW),
                         "last seen 02.01 at 08:00")
        self.assertEqual(status_text(offline(local(2024, 1, 2, 8, 0)), NOW),
                         "last seen 02.01.24")
        self.assertEqual(status_text({"@type": "userStatusRecently"}, NOW), "last seen recently")
        self.assertEqual(status_text(None, NOW), "last seen a long time ago")

    def test_typing_text(self) -> None:
        names = {1: "Olena Koval", 2: "Petr", 3: "Jana"}.get
        typing = [Typing(1, "chatActionTyping")]
        self.assertEqual(typing_text(typing, names, private=True), "typing…")
        self.assertEqual(typing_text(typing, names, private=False), "Olena is typing…")
        voice = [Typing(1, "chatActionRecordingVoiceNote")]
        self.assertEqual(typing_text(voice, names, private=True),
                         "recording a voice message…")
        two = [Typing(1, "chatActionTyping"), Typing(2, "chatActionTyping")]
        self.assertEqual(typing_text(two, names, private=False),
                         "Olena and Petr are typing…")
        three = [*two, Typing(3, "chatActionUploadingPhoto")]
        self.assertEqual(typing_text(three, names, private=False), "3 people are typing…")
        self.assertEqual(typing_text([], names, private=False), "")

    def test_members_text(self) -> None:
        self.assertEqual(members_text(12, 3, channel=False), "12 members, 3 online")
        self.assertEqual(members_text(1, 1, channel=False), "1 member")
        self.assertEqual(members_text(2000, 0, channel=True), "2000 subscribers")
        self.assertEqual(members_text(0, 0, channel=False), "")


class PresenceStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lib = FakeLib()
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.presence = PresenceStore(self.client)
        self.events: list[tuple[str, Any]] = []
        self.presence.subscribe(lambda kind, payload: self.events.append((kind, payload)))

    async def push(self, *events: dict[str, Any]) -> None:
        seen: list[Any] = []
        off = self.client.on("updateTestMarker", seen.append)
        for event in (*events, {"@type": "updateTestMarker"}):
            self.lib.push(event)
        await wait_until(lambda: bool(seen))
        off()

    async def test_statuses_and_bots(self) -> None:
        soon = int(time.time()) + 60
        await self.push(
            {"@type": "updateUser", "user": {"id": 5, "first_name": "Olena", "status": {
                "@type": "userStatusOnline", "expires": soon}}},
            {"@type": "updateUser", "user": {"id": 6, "first_name": "Bot",
                                             "type": {"@type": "userTypeBot"}}},
        )
        self.assertTrue(self.presence.is_online(5))
        self.assertIn(6, self.presence.bots)
        await self.push({"@type": "updateUserStatus", "user_id": 5,
                         "status": {"@type": "userStatusRecently"}})
        self.assertFalse(self.presence.is_online(5))
        self.assertIn(("user", 5), self.events)

    async def test_chat_actions_add_replace_and_cancel(self) -> None:
        def action(user: int, kind: str) -> dict[str, Any]:
            return {"@type": "updateChatAction", "chat_id": 42, "topic_id": None,
                    "sender_id": {"@type": "messageSenderUser", "user_id": user},
                    "action": {"@type": kind}}

        await self.push(action(5, "chatActionTyping"), action(6, "chatActionTyping"))
        self.assertEqual([t.sender_key for t in self.presence.typing[42]], [5, 6])
        await self.push(action(5, "chatActionRecordingVoiceNote"))
        self.assertEqual([(t.sender_key, t.action) for t in self.presence.typing[42]],
                         [(6, "chatActionTyping"), (5, "chatActionRecordingVoiceNote")])
        await self.push(action(5, "chatActionCancel"), action(6, "chatActionCancel"))
        self.assertNotIn(42, self.presence.typing)
        await self.push(action(5, "chatActionWatchingAnimations"))  # not shown
        self.assertNotIn(42, self.presence.typing)

    async def test_member_counts(self) -> None:
        await self.push(
            {"@type": "updateSupergroup", "supergroup": {"id": 77, "member_count": 120,
                                                         "is_channel": True}},
            {"@type": "updateBasicGroup", "basic_group": {"id": 8, "member_count": 4}},
            {"@type": "updateChatOnlineMemberCount", "chat_id": -1008, "online_member_count": 2},
        )
        self.assertEqual(self.presence.members, {77: 120, 8: 4})
        self.assertIn(77, self.presence.channels)
        self.assertEqual(self.presence.online_count[-1008], 2)


if __name__ == "__main__":
    unittest.main()
