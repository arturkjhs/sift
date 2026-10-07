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



class FromFilterTest(PersonCase):
    def test_split_from(self) -> None:
        from tgclient.services.person_search import split_from

        self.assertEqual(split_from("from:@olena money"), ("money", "olena"))
        self.assertEqual(split_from("money from:Petr"), ("money", "Petr"))
        self.assertEqual(split_from("money"), ("money", ""))
        self.assertEqual(split_from("info:x"), ("info:x", ""))

    async def test_chat_search_with_sender(self) -> None:
        from tgclient.models.messages import MessageListModel

        for event in ({"@type": "updateUser", "user": {
                "id": 5, "first_name": "Olena", "usernames": {"active_usernames": ["olena"]}}},
                {"@type": "updateUser", "user": {"id": 6, "first_name": "Petr"}}):
            self.lib.push(event)
        await wait_until(lambda: 6 in self.users.users)
        model = MessageListModel(self.client, self.chats, self.users, forums=None)
        model.open(GROUP)
        rewritten: list[str] = []
        jumps: list[int] = []
        model.chatSearchQueryRewritten.connect(rewritten.append)
        model.chatSearchJump.connect(jumps.append)
        model.searchInChat("from:@olena money")
        await wait_until(lambda: model.chatSearchSender == "user:5" and bool(jumps))
        self.assertEqual(rewritten, ["money"])
        self.assertEqual((model.chatSearchQuery, model.chatSearchSenderName), ("money", "Olena"))
        self.assertEqual(jumps, [91])
        sent = [r for r in self.server.searches if r.get("sender_id")][-1]
        self.assertEqual((sent["query"], sent["sender_id"]["user_id"]), ("money", 5))

        model.searchInChat("")  # the chip alone: all of their messages
        await wait_until(lambda: model.chatSearchCount == 50)
        model.setChatSearchSender("user:6", "Petr")
        await wait_until(lambda: self.server.searches[-1]["sender_id"]["user_id"] == 6)
        model.endChatSearch()
        self.assertEqual((model.chatSearchSender, model.chatSearchCount), ("", 0))

    async def test_sender_picker(self) -> None:
        from tgclient.models.sender_picker import SenderPicker

        for event in ({"@type": "updateUser", "user": {
                "id": 5, "first_name": "Olena", "usernames": {"active_usernames": ["olena"]}}},
                {"@type": "updateUser", "user": {"id": 6, "first_name": "Petr"}},
                {"@type": "updateOption", "name": "my_id",
                 "value": {"@type": "optionValueInteger", "value": "1"}},
                {"@type": "updateUser", "user": {"id": 1, "first_name": "Me"}},
                new_chat(5, "Olena", 3)):
            self.lib.push(event)
        await wait_until(lambda: 5 in self.chats.chats and self.users.my_id == 1)
        picker = SenderPicker(self.client, self.chats, self.users)
        picker.find("", 5)  # a private chat: the other person and me
        await wait_until(lambda: [r["key"] for r in picker.rows] == ["user:5", "user:1"])
        picker.find("ole", 0)  # anyone known, by name or username
        await wait_until(lambda: [r["key"] for r in picker.rows] == ["user:5"])
        self.assertEqual(picker.rows[0]["username"], "olena")


class GlobalFromTest(PersonCase):
    async def test_global_search_from(self) -> None:
        from tgclient.models.search import SearchModel
        from tgclient.services.search import SearchService
        from tgclient.services.search_index import SearchIndex

        self.lib.push({"@type": "updateUser", "user": {
            "id": 5, "first_name": "Olena", "usernames": {"active_usernames": ["olena"]}}})
        await wait_until(lambda: 5 in self.users.users)
        service = SearchService(self.client, self.chats, self.users, SearchIndex(":memory:"),
                                None, None)
        captured: list[tuple[str, int, str]] = []

        async def fake_search(query: str, chat_id: int = 0, sender: str = "",
                              limit: int = 40) -> list[Any]:
            captured.append((query, chat_id, sender))
            return []

        service.search = fake_search  # type: ignore[method-assign]
        model = SearchModel(service, self.chats, client=self.client, users=self.users)
        rewritten: list[str] = []
        model.queryRewritten.connect(rewritten.append)
        model.setProperty("query", "from:@olena money")
        await wait_until(lambda: model.sender == "user:5")
        self.assertEqual((model.query, rewritten), ("money", ["money"]))
        model._run()  # the debounce timer (no Qt loop in this test)
        await wait_until(lambda: bool(captured))
        self.assertEqual(captured[-1], ("money", 0, "user:5"))
        # the server search can't filter by sender: not used for a global From: search
        self.assertEqual(await service._server_search("money", 0, "user:5"), [])
        model.setSender("", "")
        self.assertEqual(model.senderName, "")


if __name__ == "__main__":
    unittest.main()
