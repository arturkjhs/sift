"""ChatStore, formatting helpers and ChatListModel against scripted TDLib updates."""

from __future__ import annotations

import time
import unittest
from datetime import datetime
from typing import Any

from fakes import FakeLib, error, new_chat, ok, position, qt_app, wait_until

from tgclient.store.chats import ARCHIVE, MAIN, ChatStore
from tgclient.store.format import initials, message_preview, message_time
from tgclient.store.users import UserStore
from tgclient.td import TdHub


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    if req["@type"] == "loadChats":
        return [error(req, 404, "Not Found")]
    return [ok(req)]


class StoreTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lib = FakeLib(responder)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.store = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.events: list[tuple[str, Any]] = []
        self.store.subscribe(lambda kind, payload: self.events.append((kind, payload)))

    async def push(self, *events: dict[str, Any]) -> None:
        marker = {"@type": "updateTestMarker", "n": len(self.lib.sent) + time.monotonic()}
        seen: list[Any] = []
        unsubscribe = self.client.on("updateTestMarker", seen.append)
        for event in (*events, marker):
            self.lib.push(event)
        await wait_until(lambda: bool(seen))
        unsubscribe()


class ChatStoreTest(StoreTestCase):
    async def test_positions_define_list_membership_and_order(self) -> None:
        await self.push(new_chat(1, "A", 300), new_chat(2, "B", 200), new_chat(3, "C"))
        self.assertEqual([c.id for c in self.store.chats_in(MAIN)], [1, 2])

        await self.push({"@type": "updateChatPosition", "chat_id": 3, "position": position(500)})
        self.assertEqual([c.id for c in self.store.chats_in(MAIN)], [3, 1, 2])

        await self.push({"@type": "updateChatPosition", "chat_id": 1, "position": position(0)})
        self.assertEqual([c.id for c in self.store.chats_in(MAIN)], [3, 2])

        await self.push({"@type": "updateChatPosition", "chat_id": 2,
                         "position": position(10, "chatListArchive")})
        self.assertEqual([c.id for c in self.store.chats_in(ARCHIVE)], [2])

    async def test_scope_mute(self) -> None:
        await self.push(new_chat(1, "Group", 1, "chatTypeBasicGroup",
                                 notification_settings={"use_default_mute_for": True}))
        chat = self.store.chats[1]
        self.assertFalse(self.store.is_muted(chat))
        await self.push({
            "@type": "updateScopeNotificationSettings",
            "scope": {"@type": "notificationSettingsScopeGroupChats"},
            "notification_settings": {"mute_for": 100000},
        })
        self.assertTrue(self.store.is_muted(chat))

    async def test_folders_and_unread(self) -> None:
        await self.push(
            {"@type": "updateChatFolders", "main_chat_list_position": 1, "chat_folders": [
                {"id": 3, "name": {"text": {"text": "Work"}}},
                {"id": 4, "title": "Family"},
            ]},
            {"@type": "updateUnreadChatCount", "chat_list": {"@type": "chatListMain"},
             "unread_unmuted_count": 5},
        )
        self.assertEqual([(f.key, f.title) for f in self.store.folders],
                         [("folder:3", "Work"), (MAIN, "All chats"), ("folder:4", "Family")])
        self.assertEqual(self.store.unread[MAIN], 5)

    async def test_photo_path_from_file_update(self) -> None:
        photo = {"small": {"id": 77, "local": {"path": "", "is_downloading_completed": False}}}
        await self.push(new_chat(1, "A", 1, photo=photo))
        self.assertIsNone(self.store.chats[1].photo_path)
        await self.push({"@type": "updateFile", "file": {
            "id": 77, "local": {"path": "/tmp/a.jpg", "is_downloading_completed": True}}})
        self.assertEqual(self.store.chats[1].photo_path, "/tmp/a.jpg")
        self.assertEqual(self.store.file_path(77), "/tmp/a.jpg")
        self.assertIn(("chat", 1), self.events)

    async def test_load_more_marks_list_complete_on_404(self) -> None:
        await self.store.load_more(MAIN)
        self.assertTrue(self.store.is_fully_loaded(MAIN))


class FormatTest(StoreTestCase):
    async def test_preview_in_groups_has_sender(self) -> None:
        msg = {"sender_id": {"@type": "messageSenderUser", "user_id": 5}, "date": 0,
               "content": {"@type": "messageText", "text": {"text": "hello\n  world"}}}
        await self.push(
            {"@type": "updateUser", "user": {"id": 5, "first_name": "Olena", "last_name": "K"}},
            new_chat(1, "Group", 1, "chatTypeSupergroup", last_message=msg),
            new_chat(2, "Private", 1, last_message=msg),
            new_chat(3, "Mine", 1, "chatTypeBasicGroup", last_message={**msg, "is_outgoing": True}),
        )
        self.assertEqual(message_preview(self.store.chats[1], self.users), "Olena: hello world")
        self.assertEqual(message_preview(self.store.chats[2], self.users), "hello world")
        self.assertEqual(message_preview(self.store.chats[3], self.users), "You: hello world")

    async def test_time_and_initials(self) -> None:
        now = datetime(2026, 10, 1, 15, 0)  # noqa: DTZ001 - local time by design
        await self.push(new_chat(1, "A", 1, last_message={
            "date": int(datetime(2026, 10, 1, 9, 5).timestamp()),  # noqa: DTZ001
            "content": {}}))
        self.assertEqual(message_time(self.store.chats[1], now), "09:05")
        self.assertEqual(initials("Pražské IT komunita"), "PI")
        self.assertEqual(initials("🚀 Rocket"), "R")
        self.assertEqual(initials(""), "?")


class ChatListModelTest(StoreTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        qt_app()
        from PySide6.QtTest import QAbstractItemModelTester

        from tgclient.models.chat_list import ChatListModel, Role

        self.model = ChatListModel(self.store, self.users)
        self.tester = QAbstractItemModelTester(
            self.model, QAbstractItemModelTester.FailureReportingMode.Fatal)
        self.role = Role

    def ids(self) -> list[int]:
        return [self.model.data(self.model.index(r), self.role.ChatId)
                for r in range(self.model.rowCount())]

    async def test_insert_move_remove(self) -> None:
        await self.push(new_chat(1, "A", 300), new_chat(2, "B", 200), new_chat(3, "C", 100))
        self.model.setList(MAIN)
        self.assertEqual(self.ids(), [1, 2, 3])

        moves: list[tuple[int, int]] = []
        self.model.rowsMoved.connect(lambda _p, start, _e, _d, dest: moves.append((start, dest)))

        # new message in C: moves to the top
        await self.push({"@type": "updateChatLastMessage", "chat_id": 3, "last_message": None,
                         "positions": [position(400)]})
        self.assertEqual(self.ids(), [3, 1, 2])
        self.assertEqual(moves, [(2, 0)])

        # move down
        await self.push({"@type": "updateChatPosition", "chat_id": 3, "position": position(50)})
        self.assertEqual(self.ids(), [1, 2, 3])
        self.assertEqual(moves[-1], (0, 3))

        # order change without changing row: no move
        await self.push({"@type": "updateChatPosition", "chat_id": 2, "position": position(250)})
        self.assertEqual(self.ids(), [1, 2, 3])
        self.assertEqual(len(moves), 2)

        # remove + insert
        await self.push({"@type": "updateChatPosition", "chat_id": 1, "position": position(0)},
                        new_chat(4, "D", 150))
        self.assertEqual(self.ids(), [2, 4, 3])

    async def test_switching_lists(self) -> None:
        await self.push(new_chat(1, "A", 300),
                        {"@type": "updateChatPosition", "chat_id": 1,
                         "position": position(5, "chatListFolder", chat_folder_id=9)},
                        new_chat(2, "B", 200))
        self.model.setList("folder:9")
        self.assertEqual(self.ids(), [1])
        self.model.setList(MAIN)
        self.assertEqual(self.ids(), [1, 2])

    async def test_roles(self) -> None:
        await self.push(new_chat(-1001234567890, "Big Group", 1, "chatTypeSupergroup",
                                 unread_count=3))
        self.model.setList(MAIN)
        index = self.model.index(0)
        self.assertEqual(self.model.data(index, self.role.ChatId), -1001234567890)
        self.assertEqual(self.model.data(index, self.role.UnreadCount), 3)
        self.assertEqual(self.model.data(index, self.role.Initials), "BG")
        self.assertEqual(self.model.data(index, self.role.AvatarSource), "")
        names = {bytes(v).decode() for v in self.model.roleNames().values()}
        self.assertTrue({"chatId", "title", "avatarSource", "unreadCount"} <= names)


if __name__ == "__main__":
    unittest.main()
