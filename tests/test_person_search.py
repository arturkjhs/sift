"""Messages of one sender: paging in a chat, the query highlighted, forum topics, a chat as
the sender, the history scan when TDLib refuses an empty query."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import FakeLib, history_ids, new_chat, ok, qt_app, wait_until

from tgclient.services.person_search import PersonSearch
from tgclient.store.chats import ChatStore
from tgclient.store.forums import ForumStore
from tgclient.store.users import UserStore
from tgclient.td import TdHub

GROUP = -100
SECRET = 7


def msg(mid: int, sender: dict[str, Any], text: str, chat: int = GROUP,
        topic: int = 0, **fields: Any) -> dict[str, Any]:
    message = {"@type": "message", "id": mid, "chat_id": chat, "date": 1_700_000_000 + mid,
               "sender_id": sender, "is_outgoing": False,
               "content": {"@type": "messageText", "text": {"text": text, "entities": []}},
               **fields}
    if topic:
        message["topic_id"] = {"@type": "messageTopicForum", "forum_topic_id": topic}
    return message


OLENA = {"@type": "messageSenderUser", "user_id": 5}
PETR = {"@type": "messageSenderUser", "user_id": 6}
ADMINS = {"@type": "messageSenderChat", "chat_id": GROUP}
HISTORY = {i: msg(i, OLENA if i % 3 else PETR,
                  "I will return the money by Friday" if i == 91 else f"note {i}",
                  topic=7 if i % 2 else 1) for i in range(1, 121)}
HISTORY[121] = msg(121, ADMINS, "Rules updated")


class Server:
    def __init__(self) -> None:
        self.refuse_empty = False
        self.searches: list[dict[str, Any]] = []

    def __call__(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        extra = req.get("@extra")
        match req["@type"]:
            case "searchChatMessages":
                self.searches.append(req)
                if self.refuse_empty and not req["query"]:
                    return [{"@type": "error", "code": 400, "message": "SEARCH_QUERY_EMPTY",
                             "@extra": extra}]
                sender = req["sender_id"]
                ids = sorted((i for i, m in HISTORY.items()
                              if m["chat_id"] == req["chat_id"] and m["sender_id"] == sender
                              and req["query"].lower() in m["content"]["text"]["text"].lower()
                              and (not req["from_message_id"] or i < req["from_message_id"])),
                             reverse=True)[:req["limit"]]
                return [{"@type": "foundChatMessages", "total_count": len(ids),
                         "messages": [HISTORY[i] for i in ids],
                         "next_from_message_id": ids[-1] if len(ids) == req["limit"] else 0,
                         "@extra": extra}]
            case "getChatHistory":
                ids = history_ids(list(HISTORY), req)
                return [{"@type": "messages", "total_count": len(ids),
                         "messages": [HISTORY[i] for i in ids], "@extra": extra}]
            case "getForumTopics":
                return [{"@type": "forumTopics", "total_count": 2, "next_offset_date": 0,
                         "next_offset_message_id": 0, "next_offset_forum_topic_id": 0,
                         "@extra": extra, "topics": [
                             {"info": {"forum_topic_id": t, "name": name, "chat_id": GROUP},
                              "last_message": None} for t, name in ((1, "General"),
                                                                    (7, "Money"))]}]
        return [ok(req)]


class PersonCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        qt_app()
        self.server = Server()
        self.lib = FakeLib(self.server)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.forums = ForumStore(self.client)
        for event in (new_chat(GROUP, "Flatmates", 9, "chatTypeSupergroup", supergroup_id=100),
                      new_chat(SECRET, "Secret", 8, "chatTypeSecret"),
                      {"@type": "updateSupergroup", "supergroup": {"id": 100, "is_forum": True}}):
            self.lib.push(event)
        await wait_until(lambda: self.forums.is_forum(100) and SECRET in self.chats.chats)
        await self.forums.load(GROUP)
        self.search = PersonSearch(self.client, self.chats)


class PersonSearchTest(PersonCase):
    async def test_pages_and_fallback(self) -> None:
        page, next_from = await self.search.page(GROUP, "user:5", "")
        self.assertEqual(len(page), 50)
        self.assertTrue(all(m["sender_id"] == OLENA for m in page))
        self.assertEqual([m["id"] for m in page], sorted((m["id"] for m in page), reverse=True))
        more, _ = await self.search.page(GROUP, "user:5", "", next_from)
        self.assertTrue(more and more[0]["id"] < page[-1]["id"])
        self.assertEqual(await self.search.page(SECRET, "user:5"), ([], 0))  # not supported

        self.server.refuse_empty = True
        scanned, next_from = await self.search.page(GROUP, "user:5", "", 0, limit=10)
        self.assertEqual(len(scanned), 10)
        self.assertTrue(all(m["sender_id"] == OLENA for m in scanned))
        self.assertTrue(next_from)


class PersonMessagesModelTest(PersonCase):
    async def test_model(self) -> None:
        from PySide6.QtTest import QAbstractItemModelTester

        from tgclient.models.person_messages import PersonMessagesModel, Role

        model = PersonMessagesModel(self.search, self.chats, self.users, self.forums)
        tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
        self.assertIsNotNone(tester)
        model.open(GROUP, "user:5", "Olena")
        await wait_until(lambda: model.count == 50 and not model.busy)
        self.assertTrue(model.more)
        first = model.index(0)
        self.assertEqual(model.data(first, Role.Topic), "Money")  # forum: the topic shown
        model.data(model.index(model.rowCount() - 1), Role.Text)  # near the end: next page
        await wait_until(lambda: model.count > 50 and not model.busy)

        model.setProperty("query", "money")
        await wait_until(lambda: model.count == 1 and not model.busy)
        text = model.data(model.index(0), Role.Text)
        self.assertIn('background-color', text)
        self.assertIn(">money</span>", text)
        self.assertEqual(model.data(model.index(0), Role.MessageId), 91)

        model.open(GROUP, "chat:-100", "Flatmates")  # anonymous admins
        await wait_until(lambda: model.count == 1 and not model.busy)
        self.assertTrue(model.onBehalf)


if __name__ == "__main__":
    unittest.main()
