"""Groups and channels: creating, my role, the invite link, admins, removing members,
editing, deleting."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import FakeLib, new_chat, ok, qt_app, wait_until

from tgclient.store.chats import ChatStore
from tgclient.store.presence import PresenceStore
from tgclient.td import TdHub

GROUP = -300


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    extra = req.get("@extra")
    match req["@type"]:
        case "createNewBasicGroupChat":
            return [{"@type": "createdBasicGroupChat", "chat_id": -400, "@extra": extra,
                     "failed_to_add_members": {"failed_to_add_members": [{"user_id": 6}]}}]
        case "createNewSupergroupChat":
            return [{"@type": "chat", "id": -500, "@extra": extra}]
        case "getSupergroupFullInfo":
            return [{"@type": "supergroupFullInfo", "invite_link": None, "@extra": extra}]
        case "replacePrimaryChatInviteLink":
            return [{"@type": "chatInviteLink", "invite_link": "https://t.me/+NEW",
                     "@extra": extra}]
    return [ok(req)]


class GroupAdminTest(unittest.IsolatedAsyncioTestCase):
    async def test_admin(self) -> None:
        qt_app()
        from tgclient.ui.group_admin import GroupAdmin

        lib = FakeLib(responder)
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        client = hub.create_client()
        chats = ChatStore(client)
        presence = PresenceStore(client)
        for event in (new_chat(GROUP, "Hikers", 5, "chatTypeSupergroup", supergroup_id=300),
                      {"@type": "updateSupergroup", "supergroup": {
                          "id": 300, "member_count": 3,
                          "status": {"@type": "chatMemberStatusCreator", "is_member": True}}}):
            lib.push(event)
        await wait_until(lambda: 300 in presence.group_status)
        admin = GroupAdmin(client, chats, presence)
        sent = lambda kind: [r for r in lib.sent if r["@type"] == kind]  # noqa: E731
        self.assertEqual(admin.role(GROUP), "owner")

        created: list[Any] = []
        failed: list[str] = []
        admin.created.connect(created.append)
        admin.failed.connect(failed.append)
        admin.create("group", "Friends", "", [5, 6])
        admin.create("channel", "News", "Daily", [])
        admin.create("group", "  ", "", [])
        await wait_until(lambda: len(created) == 2 and len(failed) == 2)
        self.assertEqual(sorted(created), [-500, -400])
        self.assertIn("couldn't be added", " ".join(failed))
        self.assertTrue(sent("createNewSupergroupChat")[0]["is_channel"])

        self.assertEqual(admin.inviteLink(GROUP), "")  # asks: none yet, so a new one is made
        await wait_until(lambda: admin.inviteLink(GROUP) == "https://t.me/+NEW")

        admin.setAdmin(GROUP, 5, True)
        admin.setAdmin(GROUP, 5, False)
        admin.removeMember(GROUP, 6)
        admin.addMembers(GROUP, [7])
        admin.editInfo(GROUP, "Hikers & climbers", "Weekends")
        admin.deleteChat(GROUP)
        await wait_until(lambda: bool(sent("deleteChat")))
        promote, demote = sent("setChatMemberStatus")
        self.assertEqual((promote["status"]["@type"], demote["status"]["@type"]),
                         ("chatMemberStatusAdministrator", "chatMemberStatusMember"))
        self.assertFalse(promote["status"]["rights"]["can_promote_members"])
        self.assertEqual(sent("banChatMember")[0]["member_id"]["user_id"], 6)
        self.assertEqual(sent("setChatTitle")[0]["title"], "Hikers & climbers")
        self.assertEqual(sent("addChatMembers")[0]["user_ids"], [7])


if __name__ == "__main__":
    unittest.main()
