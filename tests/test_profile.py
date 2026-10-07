"""The profile panel's model: a person (bio, groups in common), a group (description,
members), what was shared in the chat by tab."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import FakeLib, new_chat, ok, qt_app, wait_until

from tgclient.store.chats import ChatStore
from tgclient.store.presence import PresenceStore
from tgclient.store.users import UserStore
from tgclient.td import TdHub

GROUP = -500


def photo(mid: int) -> dict[str, Any]:
    return {"@type": "message", "id": mid, "chat_id": GROUP, "date": 1_700_000_000 + mid,
            "sender_id": {"@type": "messageSenderUser", "user_id": 5}, "content": {
                "@type": "messagePhoto", "caption": {"text": ""}, "photo": {"sizes": [{
                    "type": "m", "width": 320, "height": 200, "photo": {
                        "id": 900 + mid, "size": 10, "expected_size": 10,
                        "local": {"path": "", "is_downloading_completed": False},
                        "remote": {}}}]}}}


def link(mid: int) -> dict[str, Any]:
    text = "📌 see https://example.com/a"
    return {"@type": "message", "id": mid, "chat_id": GROUP, "date": 1_700_000_000 + mid,
            "sender_id": {"@type": "messageSenderUser", "user_id": 5}, "content": {
                "@type": "messageText", "text": {"text": text, "entities": [
                    {"offset": 7, "length": 21, "type": {"@type": "textEntityTypeUrl"}}]}}}


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    extra = req.get("@extra")
    match req["@type"]:
        case "downloadFile":
            return [{"@type": "file", "id": req["file_id"], "size": 10, "expected_size": 10,
                     "local": {"path": "", "is_downloading_active": True}, "remote": {},
                     "@extra": extra}]
        case "getUserFullInfo":
            return [{"@type": "userFullInfo", "bio": {"text": "Climbs on weekends"},
                     "group_in_common_count": 1, "@extra": extra}]
        case "getGroupsInCommon":
            return [{"@type": "chats", "total_count": 1, "chat_ids": [GROUP], "@extra": extra}]
        case "getSupergroupFullInfo":
            return [{"@type": "supergroupFullInfo", "description": "Weekend trips",
                     "member_count": 2, "can_get_members": True, "@extra": extra}]
        case "searchChatMembers":
            return [{"@type": "chatMembers", "total_count": 2, "@extra": extra, "members": [
                {"member_id": {"@type": "messageSenderUser", "user_id": 6},
                 "status": {"@type": "chatMemberStatusMember"}},
                {"member_id": {"@type": "messageSenderUser", "user_id": 5},
                 "status": {"@type": "chatMemberStatusCreator"}}]}]
        case "searchChatMessages":
            kind = req["filter"]["@type"]
            found = {"searchMessagesFilterPhotoAndVideo": [photo(3), photo(2)],
                     "searchMessagesFilterUrl": [link(4)]}.get(kind, [])
            if req["from_message_id"]:
                found = []
            return [{"@type": "foundChatMessages", "total_count": len(found), "messages": found,
                     "next_from_message_id": found[-1]["id"] if found else 0, "@extra": extra}]
    return [ok(req)]


class ProfileTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        qt_app()
        from tgclient.models.profile import ProfileModel

        self.lib = FakeLib(responder)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.presence = PresenceStore(self.client)
        for event in (
                new_chat(GROUP, "Climbers", 3, "chatTypeSupergroup", supergroup_id=500),
                new_chat(5, "Olena", 2),
                {"@type": "updateSupergroup", "supergroup": {
                    "id": 500, "member_count": 2, "usernames": {
                        "active_usernames": ["climbers"]}}},
                {"@type": "updateUser", "user": {"id": 5, "first_name": "Olena",
                                                 "phone_number": "420123",
                                                 "usernames": {"active_usernames": ["olena"]}}},
                {"@type": "updateUser", "user": {"id": 6, "first_name": "Petr"}}):
            self.lib.push(event)
        await wait_until(lambda: 6 in self.users.users and GROUP in self.chats.chats)
        self.profile = ProfileModel(self.client, self.chats, self.users, self.presence)

    async def test_person(self) -> None:
        self.profile.open(0, 5)
        self.assertTrue(self.profile.isUser)
        self.assertEqual(self.profile.chatId, 5)  # their private chat
        await wait_until(lambda: self.profile.description == "Climbs on weekends")
        self.assertEqual((self.profile.title, self.profile.username, self.profile.phone),
                         ("Olena", "olena", "+420123"))
        await wait_until(lambda: len(self.profile.commonGroups) == 1)
        self.assertEqual(self.profile.commonGroups[0]["title"], "Climbers")

    async def test_group_members_and_shared(self) -> None:
        self.profile.open(GROUP, 0)
        await wait_until(lambda: len(self.profile.members) == 2)
        self.assertEqual([(m["name"], m["role"]) for m in self.profile.members],
                         [("Olena", "owner"), ("Petr", "")])
        self.assertEqual((self.profile.description, self.profile.username,
                          self.profile.subtitle), ("Weekend trips", "climbers", "2 members"))
        await wait_until(lambda: len(self.profile.shared) == 2)
        self.assertEqual([r["kind"] for r in self.profile.shared], ["photo", "photo"])
        self.profile.loadMoreShared()  # the end: nothing more
        self.profile.setTab("links")
        await wait_until(lambda: len(self.profile.shared) == 1)
        row = self.profile.shared[0]
        self.assertEqual((row["url"], row["kind"]), ("https://example.com/a", "link"))
        self.profile.setTab("voice")
        await wait_until(lambda: not self.profile.sharedBusy)
        self.assertEqual(self.profile.shared, [])


if __name__ == "__main__":
    unittest.main()
