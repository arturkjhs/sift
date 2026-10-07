"""Forum supergroups: topics store, the topic list model, a topic's history, sending into a
topic, jumping to a message from the topic list, a summary of one topic."""

from __future__ import annotations

import time
import unittest
from typing import Any

from fakes import FakeLib, FakeRouter, history_ids, new_chat, ok, qt_app, wait_until

from tgclient.store.chats import ChatStore
from tgclient.store.forums import ForumStore, message_topic_id
from tgclient.store.users import UserStore
from tgclient.td import TdHub

FORUM = -100500
SUPERGROUP = 500
NOW = int(time.time())


def msg(mid: int, topic: int, text: str = "", out: bool = False) -> dict[str, Any]:
    return {"@type": "message", "id": mid, "chat_id": FORUM, "is_outgoing": out,
            "date": NOW - 1000 + mid, "topic_id": {"@type": "messageTopicForum",
                                                   "forum_topic_id": topic},
            "sender_id": {"@type": "messageSenderUser", "user_id": 5},
            "content": {"@type": "messageText", "text": {"text": text or f"t{topic} m{mid}",
                                                         "entities": []}}}


# topic 1 (General): odd ids; topic 7 ("Hiking"): even ids
MESSAGES = {i: msg(i, 1 if i % 2 else 7) for i in range(1, 61)}


def topic(topic_id: int, name: str, last: int, unread: int = 0, pinned: bool = False,
          read: int = 0) -> dict[str, Any]:
    return {"@type": "forumTopic", "info": {
        "@type": "forumTopicInfo", "chat_id": FORUM, "forum_topic_id": topic_id, "name": name,
        "icon": {"color": 0xFF93B2, "custom_emoji_id": "0"}, "is_general": topic_id == 1,
        "is_closed": False, "is_hidden": False},
        "last_message": MESSAGES[last], "order": str(last), "is_pinned": pinned,
        "unread_count": unread, "unread_mention_count": 0, "unread_reaction_count": 0,
        "last_read_inbox_message_id": read, "last_read_outbox_message_id": 0}


class Server:
    def __init__(self) -> None:
        self.topics = [topic(1, "General", 59), topic(7, "Hiking", 60, unread=4, read=52)]

    def __call__(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        extra = req.get("@extra")
        match req["@type"]:
            case "getForumTopics":
                return [{"@type": "forumTopics", "total_count": len(self.topics),
                         "topics": self.topics, "next_offset_date": 0,
                         "next_offset_message_id": 0, "next_offset_forum_topic_id": 0,
                         "@extra": extra}]
            case "getForumTopic":
                found = [t for t in self.topics
                         if t["info"]["forum_topic_id"] == req["forum_topic_id"]]
                return [{**found[0], "@extra": extra}]
            case "getForumTopicHistory":
                ids = [i for i in MESSAGES if message_topic_id(MESSAGES[i]) ==
                       req["forum_topic_id"]]
                page = [MESSAGES[i] for i in history_ids(ids, req)]
                return [{"@type": "messages", "total_count": len(page), "messages": page,
                         "@extra": extra}]
            case "getMessage":
                return [{**MESSAGES[req["message_id"]], "@extra": extra}]
            case "parseMarkdown":
                return [{**req["text"], "@extra": extra}]
            case "sendMessage":
                topic_id = (req.get("topic_id") or {}).get("forum_topic_id", 1)
                sent = msg(100, topic_id, req["input_message_content"]["text"]["text"], True)
                return [{"@type": "updateNewMessage", "message": sent}, {**sent, "@extra": extra}]
            case "getChatHistory":
                raise AssertionError("a forum's history is read per topic")
        return [ok(req)]


class ForumCase(unittest.IsolatedAsyncioTestCase):
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
        await self.push(
            new_chat(FORUM, "Climbers", 5, "chatTypeSupergroup", supergroup_id=SUPERGROUP,
                     unread_count=4, last_message=MESSAGES[60]),
            {"@type": "updateSupergroup", "supergroup": {"id": SUPERGROUP, "is_forum": True,
                                                         "is_channel": False,
                                                         "member_count": 40}},
            {"@type": "updateUser", "user": {"id": 5, "first_name": "Olena"}})

    async def push(self, *events: dict[str, Any]) -> None:
        seen: list[Any] = []
        off = self.client.on("updateTestMarker", seen.append)
        for event in (*events, {"@type": "updateTestMarker"}):
            self.lib.push(event)
        await wait_until(lambda: bool(seen))
        off()

    def sent(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.lib.sent if r["@type"] == kind]


class ForumStoreTest(ForumCase):
    async def test_topics_load_and_follow_updates(self) -> None:
        self.assertTrue(self.forums.is_forum(SUPERGROUP))
        await self.forums.load(FORUM)
        self.assertEqual([t.name for t in self.forums.sorted_topics(FORUM)],
                         ["Hiking", "General"])
        await self.push({"@type": "updateForumTopicInfo", "info": {
            "chat_id": FORUM, "forum_topic_id": 7, "name": "Hiking & trips",
            "icon": {"color": 0x6FB9F0, "custom_emoji_id": "0"}, "is_closed": True}})
        hiking = self.forums.get(FORUM, 7)
        self.assertEqual((hiking.name, hiking.is_closed), ("Hiking & trips", True))
        await self.push({"@type": "updateNewMessage", "message": msg(61, 1, "new")})
        self.assertEqual(self.forums.sorted_topics(FORUM)[0].id, 1)  # General moved up
        self.server.topics[1]["unread_count"] = 1
        await self.push({"@type": "updateForumTopic", "chat_id": FORUM, "forum_topic_id": 7,
                         "is_pinned": True, "last_read_inbox_message_id": 58,
                         "unread_mention_count": 0, "unread_reaction_count": 0})
        await wait_until(lambda: hiking.unread_count == 1)
        self.assertEqual(self.forums.sorted_topics(FORUM)[0].id, 7)  # pinned first
        await self.push({"@type": "updateSupergroup", "supergroup": {
            "id": SUPERGROUP, "is_forum": False, "is_channel": False}})
        self.assertFalse(self.forums.is_forum(SUPERGROUP))


class ForumModelTest(ForumCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from PySide6.QtTest import QAbstractItemModelTester

        from tgclient.models.composer import ComposerModel
        from tgclient.models.messages import MessageListModel
        from tgclient.models.topics import Role as TopicRole
        from tgclient.models.topics import TopicListModel

        self.model = MessageListModel(self.client, self.chats, self.users, forums=self.forums)
        self.topics = TopicListModel(self.forums, self.chats, self.users, self.model)
        self.composer = ComposerModel(self.client, self.chats, self.model)
        self.TopicRole = TopicRole
        self.testers = [QAbstractItemModelTester(
            m, QAbstractItemModelTester.FailureReportingMode.Fatal)
            for m in (self.model, self.topics)]

    async def test_topic_list_then_topic_history_and_sending(self) -> None:
        self.model.open(FORUM)
        self.assertTrue(self.model.isForum and self.model.topicsMode)
        self.assertEqual(self.model.rowCount(), 0)
        await wait_until(lambda: self.topics.rowCount() == 2)
        index = self.topics.index(0)
        self.assertEqual(self.topics.data(index, self.TopicRole.Name), "Hiking")
        self.assertEqual(self.topics.data(index, self.TopicRole.UnreadCount), 4)
        self.assertEqual(self.topics.data(index, self.TopicRole.Preview), "Olena: t7 m60")
        self.assertEqual(self.topics.data(index, self.TopicRole.IconColor), "#ff93b2")

        unread: list[int] = []
        self.model.unreadReady.connect(unread.append)
        self.model.openTopic(7)
        self.assertFalse(self.model.topicsMode)
        self.assertEqual((self.model.topicId, self.model.topicName), (7, "Hiking"))
        await wait_until(lambda: bool(unread))
        self.assertEqual(unread, [54])  # first unread in the topic (read up to 52)
        ids = [m["id"] for m in self.model._history.messages]
        self.assertTrue(ids and all(i % 2 == 0 for i in ids))
        self.assertEqual(self.model.unreadCount, 4)

        await self.push({"@type": "updateNewMessage", "message": msg(61, 1, "general")})
        self.assertEqual(self.model.rowOf(61), -1)  # another topic

        self.model.sendMessage("to hiking", 0, {})
        await wait_until(lambda: bool(self.sent("sendMessage")))
        self.assertEqual(self.sent("sendMessage")[0]["topic_id"],
                         {"@type": "messageTopicForum", "forum_topic_id": 7})
        await wait_until(lambda: self.model.rowOf(100) >= 0)

        self.composer.setDraft("draft in hiking", 0)
        await self.composer.close()
        draft = self.sent("setChatDraftMessage")[-1]
        self.assertEqual(draft["topic_id"]["forum_topic_id"], 7)

        self.model.closeTopic()
        self.assertTrue(self.model.topicsMode)
        self.assertEqual((self.model.rowCount(), self.model.topicId), (0, 0))

    async def test_jump_from_the_topic_list_opens_the_topic(self) -> None:
        self.model.open(FORUM)
        rows: list[int] = []
        self.model.jumpReady.connect(rows.append)
        self.model.jumpTo(21)  # in General
        await wait_until(lambda: bool(rows))
        self.assertEqual(self.model.topicId, 1)
        self.assertEqual(rows[-1], self.model.rowOf(21))


class TopicSummaryTest(ForumCase):
    async def test_summary_reads_only_the_topic(self) -> None:
        from tgclient.services.ai import AiService
        from tgclient.services.ai_store import AiStore
        from tgclient.ui.ai_controller import AiController

        router = FakeRouter("**Trip.** Saturday [m60].")
        service = AiService(self.client, self.chats, self.users, AiStore(":memory:"),
                            router.client(), "m/main", "m/audio")
        self.addAsyncCleanup(service.close)
        controller = AiController(service, self.chats)
        await self.forums.load(FORUM)
        controller.topic_info = lambda chat, topic_id: ("Hiking", 52)
        service.set_enabled(FORUM, True)
        controller.setProperty("chatId", FORUM)
        controller.setProperty("topicId", 7)
        self.assertEqual(controller.subject, "topic:7")
        controller.summarize("unread")
        await wait_until(lambda: service.summary(FORUM, "topic:7").state == "done")
        self.assertTrue(self.sent("getForumTopicHistory"))
        user = router.requests[0]["messages"][1]["content"]
        self.assertIn("Chat: Climbers › Hiking", user)
        lines = [line.split()[0] for line in user.splitlines() if line.startswith("[m")]
        self.assertEqual(lines, ["[m54]", "[m56]", "[m58]", "[m60]"])
        self.assertEqual(service.summary(FORUM).state, "")  # the chat's own summary untouched


if __name__ == "__main__":
    unittest.main()
