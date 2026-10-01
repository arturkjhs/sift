"""ChatHistory paging/updates and MessageListModel roles against a scripted TDLib."""

from __future__ import annotations

import time
import unittest
from typing import Any

from fakes import FakeLib, error, new_chat, ok, qt_app, wait_until

from tgclient.store.chats import ChatStore
from tgclient.store.history import ChatHistory
from tgclient.store.users import UserStore
from tgclient.td import TdHub

CHAT = 42
NOW = int(time.time())


def msg(mid: int, text: str = "", sender: int = 5, out: bool = False, ago: int = 0,
        **fields: Any) -> dict[str, Any]:
    return {"@type": "message", "id": mid, "chat_id": CHAT, "is_outgoing": out,
            "sender_id": {"@type": "messageSenderUser", "user_id": sender},
            "date": NOW - ago,
            "content": {"@type": "messageText", "text": {"text": text or f"m{mid}", "entities": []}},
            **fields}


class Server:
    """Fake chat history: ids 1..total; first call from 0 returns only the newest (like TDLib)."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.calls: list[int] = []
        self.replied: dict[int, dict[str, Any]] = {}

    def __call__(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        match req["@type"]:
            case "getChatHistory":
                start = req["from_message_id"]
                self.calls.append(start)
                if start == 0:
                    ids = [self.total]
                else:
                    ids = list(range(start, max(0, start - req["limit"]), -1))
                    ids = [i for i in ids if i >= 1]
                return [{"@type": "messages", "total_count": len(ids),
                         "messages": [msg(i, ago=(self.total - i) * 60) for i in ids],
                         "@extra": req["@extra"]}]
            case "getRepliedMessage":
                reply = self.replied.get(req["message_id"])
                if reply is None:
                    return [error(req, 404, "Not Found")]
                return [{**reply, "@extra": req["@extra"]}]
            case "parseMarkdown":
                return [{**req["text"], "@extra": req["@extra"]}]
            case "sendMessage":
                sent = msg(10_000, req["input_message_content"]["text"]["text"], out=True,
                           sending_state={"@type": "messageSendingStatePending"})
                return [{"@type": "updateNewMessage", "message": sent},
                        {**sent, "@extra": req["@extra"]}]
        return [ok(req)]


class HistoryCase(unittest.IsolatedAsyncioTestCase):
    total = 120

    async def asyncSetUp(self) -> None:
        self.server = Server(self.total)
        self.lib = FakeLib(self.server)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)

    async def push(self, *events: dict[str, Any]) -> None:
        seen: list[Any] = []
        off = self.client.on("updateTestMarker", seen.append)
        for event in (*events, {"@type": "updateTestMarker"}):
            self.lib.push(event)
        await wait_until(lambda: bool(seen))
        off()


class ChatHistoryTest(HistoryCase):
    async def test_initial_load_keeps_asking_until_page_is_full(self) -> None:
        history = ChatHistory(self.client, CHAT)
        await history.load_initial()
        ids = [m["id"] for m in history.messages]
        self.assertEqual(ids[0], 120)
        self.assertGreaterEqual(len(ids), ChatHistory.PAGE)
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(self.server.calls[:2], [0, 120])

    async def test_load_older_until_start(self) -> None:
        history = ChatHistory(self.client, CHAT)
        await history.load_initial()
        while not history.reached_start:
            await history.load_older()
        self.assertEqual([m["id"] for m in history.messages], list(range(120, 0, -1)))

    async def test_live_updates(self) -> None:
        history = ChatHistory(self.client, CHAT)
        await history.load_initial()
        await self.push({"@type": "updateNewMessage", "message": msg(121, "new")})
        self.assertEqual(history.messages[0]["id"], 121)

        await self.push({"@type": "updateNewMessage", "message": msg(
            9999, "pending", out=True, sending_state={"@type": "messageSendingStatePending"})})
        self.assertEqual(history.messages[0]["id"], 9999)
        await self.push({"@type": "updateMessageSendSucceeded", "old_message_id": 9999,
                         "message": msg(122, "pending", out=True)})
        self.assertEqual([m["id"] for m in history.messages[:2]], [122, 121])
        self.assertEqual(history.row_of(9999), -1)

        await self.push({"@type": "updateMessageContent", "chat_id": CHAT, "message_id": 121,
                         "new_content": {"@type": "messageText", "text": {"text": "edited"}}})
        self.assertEqual(history.get(121)["content"]["text"]["text"], "edited")

        await self.push({"@type": "updateDeleteMessages", "chat_id": CHAT, "message_ids": [121],
                         "is_permanent": True, "from_cache": False})
        self.assertIsNone(history.get(121))
        await self.push({"@type": "updateDeleteMessages", "chat_id": CHAT, "message_ids": [122],
                         "is_permanent": False, "from_cache": True})
        self.assertIsNotNone(history.get(122))

        await self.push({"@type": "updateNewMessage", "message": {**msg(500), "chat_id": 7}})
        self.assertIsNone(history.get(500))  # other chat

    async def test_reply_is_fetched_and_cached(self) -> None:
        self.server.replied[120] = msg(3, "the original")
        history = ChatHistory(self.client, CHAT)
        await history.load_initial()
        changed: list[int] = []

        class Listener:
            def history_insert(self, row, count, commit): commit()
            def history_remove(self, row, count, commit): commit()
            def history_changed(self, row): changed.append(row)

        history._listener = Listener()
        newest = {**history.messages[0], "reply_to": {
            "@type": "messageReplyToMessage", "chat_id": CHAT, "message_id": 3}}
        history.messages[0] = newest
        self.assertIsNone(history.reply_message(newest))
        await wait_until(lambda: 0 in changed)
        self.assertEqual(history.reply_message(newest)["id"], 3)


class MessageModelTest(HistoryCase):
    total = 6

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        qt_app()
        from PySide6.QtTest import QAbstractItemModelTester

        from tgclient.models.messages import MessageListModel, Role

        self.Role = Role
        await self.push(
            new_chat(CHAT, "Friends", 1, "chatTypeSupergroup", last_read_outbox_message_id=0),
            {"@type": "updateUser", "user": {"id": 5, "first_name": "Olena", "last_name": "K"}},
        )
        self.model = MessageListModel(self.client, self.chats, self.users)
        self.tester = QAbstractItemModelTester(
            self.model, QAbstractItemModelTester.FailureReportingMode.Fatal)

    def role(self, row: int, role: Any) -> Any:
        return self.model.data(self.model.index(row), role)

    async def open_loaded(self) -> None:
        self.model.open(CHAT)
        await wait_until(lambda: not self.model.loading and self.model.rowCount() == 6)

    async def test_open_load_and_grouping(self) -> None:
        await self.open_loaded()
        self.assertIn({"@type": "openChat", "chat_id": CHAT},
                      [{k: v for k, v in r.items() if k != "@extra"} for r in self.lib.sent])
        # all 6 from the same sender within minutes: one group
        self.assertTrue(self.role(5, self.Role.ShowSender))       # oldest shows the name
        self.assertFalse(self.role(0, self.Role.ShowSender))
        self.assertTrue(self.role(0, self.Role.GroupBottom))      # newest closes the group
        self.assertTrue(self.role(0, self.Role.ShowAvatar))
        self.assertFalse(self.role(3, self.Role.GroupBottom))
        # small chat: the start is reached during the initial load, so the oldest gets a label
        self.assertTrue(self.model._history.reached_start)
        self.assertEqual(self.role(5, self.Role.DayLabel), "Today")
        self.assertEqual(self.role(4, self.Role.DayLabel), "")

    async def test_html_and_status(self) -> None:
        await self.open_loaded()
        html = self.role(0, self.Role.Html)
        self.assertIn("m6", html)
        self.assertIn("white-space:pre-wrap", html)

        await self.push({"@type": "updateNewMessage", "message": msg(7, "mine", sender=1, out=True)})
        self.assertEqual(self.role(0, self.Role.Status), "sent")
        self.assertTrue(self.role(1, self.Role.GroupBottom))  # neighbor recomputed
        await self.push({"@type": "updateChatReadOutbox", "chat_id": CHAT,
                         "last_read_outbox_message_id": 7})
        self.assertEqual(self.role(0, self.Role.Status), "read")

    async def test_send_with_reply_and_markdown(self) -> None:
        await self.open_loaded()
        self.model.send("**hi**", 6)
        await wait_until(lambda: self.model.rowCount() == 7)
        sent = next(r for r in self.lib.sent if r["@type"] == "sendMessage")
        self.assertEqual(sent["reply_to"], {"@type": "inputMessageReplyToMessage", "message_id": 6})
        self.assertTrue(any(r["@type"] == "parseMarkdown" for r in self.lib.sent))
        self.assertEqual(self.role(0, self.Role.Status), "pending")

    async def test_mark_viewed_only_incoming_once(self) -> None:
        await self.open_loaded()
        self.model.markViewed(0, 2)
        self.model.markViewed(2, 0)
        await wait_until(lambda: any(r["@type"] == "viewMessages" for r in self.lib.sent))
        views = [r for r in self.lib.sent if r["@type"] == "viewMessages"]
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0]["message_ids"], [6, 5, 4])

    async def test_switching_chats_ignores_late_pages(self) -> None:
        self.model.open(CHAT)
        self.model.open(0)  # close before the first page arrives
        await wait_until(lambda: any(r["@type"] == "closeChat" for r in self.lib.sent))
        self.assertEqual(self.model.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
