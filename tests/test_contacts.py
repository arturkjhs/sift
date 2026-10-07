"""Contacts: list and search, opening a chat, adding by phone or from a profile, removing."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import FakeLib, ok, qt_app, wait_until

from tgclient.store.files import FileManager
from tgclient.store.presence import PresenceStore
from tgclient.store.users import UserStore
from tgclient.td import TdHub


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    extra = req.get("@extra")
    match req["@type"]:
        case "getContacts":
            return [{"@type": "users", "total_count": 2, "user_ids": [5, 6], "@extra": extra}]
        case "searchContacts":
            ids = [5] if req["query"].lower().startswith("ol") else []
            return [{"@type": "users", "total_count": len(ids), "user_ids": ids, "@extra": extra}]
        case "createPrivateChat":
            return [{"@type": "chat", "id": req["user_id"], "@extra": extra}]
        case "importContacts":
            known = req["contacts"][0]["phone_number"] == "+420111"
            return [{"@type": "importedContacts", "user_ids": [7] if known else [0],
                     "importer_count": [0], "@extra": extra}]
    return [ok(req)]


class ContactsTest(unittest.IsolatedAsyncioTestCase):
    async def test_contacts(self) -> None:
        qt_app()
        from tgclient.models.contacts import ContactsModel

        lib = FakeLib(responder)
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        client = hub.create_client()
        files = FileManager(client)
        users = UserStore(client, files)
        presence = PresenceStore(client)
        for user in ({"id": 5, "first_name": "Olena", "is_contact": True},
                     {"id": 6, "first_name": "Adam", "is_contact": True,
                      "status": {"@type": "userStatusOnline", "expires": 2**31 - 1}},
                     {"id": 7, "first_name": "Jana", "phone_number": "420111"}):
            lib.push({"@type": "updateUser", "user": user})
        await wait_until(lambda: 7 in users.users)
        contacts = ContactsModel(client, users, files, presence)
        contacts.search("")
        await wait_until(lambda: len(contacts.rows) == 2)
        self.assertEqual([r["name"] for r in contacts.rows], ["Adam", "Olena"])  # online first
        self.assertEqual(contacts.rows[0]["status"], "online")
        contacts.search("ol")
        await wait_until(lambda: [r["userId"] for r in contacts.rows] == [5])

        opened: list[Any] = []
        failed: list[str] = []
        contacts.chatReady.connect(opened.append)
        contacts.failed.connect(failed.append)
        contacts.openChat(5)
        await wait_until(lambda: opened == [5])
        contacts.add("+420999", "Nobody", "")
        await wait_until(lambda: bool(failed))
        self.assertIn("isn't on Telegram", failed[0])
        contacts.add("+420111", "Jana", "N")
        await wait_until(lambda: opened == [5, 7])
        contacts.addFromProfile(7, False)
        contacts.remove(6)
        await wait_until(lambda: any(r["@type"] == "removeContacts" for r in lib.sent))
        added = next(r for r in lib.sent if r["@type"] == "addContact")
        self.assertEqual((added["user_id"], added["contact"]["phone_number"]), (7, "420111"))


if __name__ == "__main__":
    unittest.main()
