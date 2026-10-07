"""The chat list's context menu: pin, mute, archive, read/unread, clear, leave/delete."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import FakeLib, new_chat, ok, position, qt_app, wait_until

from tgclient.store.chats import ChatStore
from tgclient.td import TdHub


class ChatActionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        qt_app()
        from tgclient.ui.chat_actions import ChatActions

        self.lib = FakeLib(lambda req: [ok(req)])
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        settings = {"@type": "chatNotificationSettings", "use_default_mute_for": True,
                    "mute_for": 0, "use_default_sound": False, "sound_id": "5"}
        await self.push(
            new_chat(1, "Olena", 10, unread_count=3, unread_mention_count=1,
                     last_message={"id": 77, "date": 1}, notification_settings=settings,
                     can_be_deleted_for_all_users=True),
            {**new_chat(2, "Prague IT", 0, "chatTypeSupergroup"),
             "chat": {**new_chat(2, "Prague IT", 0, "chatTypeSupergroup")["chat"],
                      "positions": [position(20, pinned=True)]}})
        self.actions = ChatActions(self.client, self.chats)

    async def push(self, *events: dict[str, Any]) -> None:
        seen: list[Any] = []
        off = self.client.on("updateTestMarker", seen.append)
        for event in (*events, {"@type": "updateTestMarker"}):
            self.lib.push(event)
        await wait_until(lambda: bool(seen))
        off()

    def sent(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.lib.sent if r["@type"] == kind]

    async def test_actions(self) -> None:
        state = self.actions.state(1)
        self.assertEqual((state["pinned"], state["muted"], state["unread"], state["archived"]),
                         (False, False, True, False))
        self.assertTrue(self.actions.state(2)["pinned"])

        self.actions.setPinned(1, "main", True)
        self.actions.mute(1, 3600)
        self.actions.mute(1, -1)
        self.actions.setArchived(1, True)
        self.actions.setUnread(1, False)
        self.actions.clearHistory(1, True)
        left: list[Any] = []
        self.actions.left.connect(left.append)
        self.actions.leave(2)
        self.actions.leave(1, True)
        await wait_until(lambda: len(self.sent("deleteChatHistory")) == 2)

        pin = self.sent("toggleChatIsPinned")[0]
        self.assertEqual((pin["chat_list"], pin["is_pinned"]), ({"@type": "chatListMain"}, True))
        mutes = self.sent("setChatNotificationSettings")
        self.assertEqual([m["notification_settings"]["mute_for"] for m in mutes],
                         [3600, 366 * 86400])
        kept = mutes[0]["notification_settings"]
        self.assertEqual((kept["use_default_mute_for"], kept["sound_id"]), (False, "5"))
        self.assertEqual(self.sent("addChatToList")[0]["chat_list"], {"@type": "chatListArchive"})
        self.assertEqual(self.sent("viewMessages")[0]["message_ids"], [77])
        self.assertTrue(self.sent("readAllChatMentions"))
        clear, delete = self.sent("deleteChatHistory")
        self.assertEqual((clear["remove_from_chat_list"], clear["revoke"]), (False, True))
        self.assertEqual((delete["remove_from_chat_list"], delete["revoke"]), (True, True))
        self.assertEqual(self.sent("leaveChat")[0]["chat_id"], 2)
        self.assertEqual(left, [2, 1])

        self.actions.setUnread(2, True)
        await wait_until(lambda: bool(self.sent("toggleChatIsMarkedAsUnread")))
        await self.push({"@type": "updateChatIsMarkedAsUnread", "chat_id": 2,
                         "is_marked_as_unread": True})
        self.assertTrue(self.actions.state(2)["unread"])


if __name__ == "__main__":
    unittest.main()
