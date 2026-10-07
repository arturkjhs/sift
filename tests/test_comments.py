"""Comments to a channel post: the count on the post, the thread in the discussion group,
sending into it and going back to the channel."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import FakeLib, history_ids, new_chat, ok, qt_app, wait_until

from tgclient.store.chats import ChatStore
from tgclient.store.users import UserStore
from tgclient.td import TdHub

CHANNEL = -100
GROUP = -200
THREAD = 500  # the post's copy in the discussion group


def post(mid: int, comments: int | None) -> dict[str, Any]:
    info = {"reply_info": {"reply_count": comments, "recent_replier_ids": [],
                           "last_read_inbox_message_id": 0, "last_message_id": 0}
            } if comments is not None else {}
    return {"@type": "message", "id": mid, "chat_id": CHANNEL, "date": 1_700_000_000 + mid,
            "is_outgoing": False, "is_channel_post": True,
            "sender_id": {"@type": "messageSenderChat", "chat_id": CHANNEL},
            "content": {"@type": "messageText", "text": {"text": f"post {mid}"}},
            "interaction_info": info}


def comment(mid: int) -> dict[str, Any]:
    return {"@type": "message", "id": mid, "chat_id": GROUP, "date": 1_700_000_000 + mid,
            "is_outgoing": False, "sender_id": {"@type": "messageSenderUser", "user_id": 5},
            "topic_id": {"@type": "messageTopicThread", "message_thread_id": THREAD},
            "content": {"@type": "messageText", "text": {"text": f"comment {mid}"}}}


POSTS = {i: post(i, 3 if i == 20 else None) for i in range(1, 31)}
COMMENTS = {THREAD: comment(THREAD), **{i: comment(i) for i in (501, 502, 503)}}


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    extra = req.get("@extra")
    match req["@type"]:
        case "getChatHistory":
            source = POSTS if req["chat_id"] == CHANNEL else {}
            page = [source[i] for i in history_ids(list(source), req)]
            return [{"@type": "messages", "total_count": len(page), "messages": page,
                     "@extra": extra}]
        case "getMessageThread":
            return [{"@type": "messageThreadInfo", "chat_id": GROUP, "message_thread_id": THREAD,
                     "reply_info": {"reply_count": 3, "last_read_inbox_message_id": 501},
                     "unread_message_count": 2, "messages": [COMMENTS[THREAD]],
                     "@extra": extra}]
        case "getMessageThreadHistory":
            assert req["message_id"] == THREAD
            page = [COMMENTS[i] for i in history_ids(list(COMMENTS), req)]
            return [{"@type": "messages", "total_count": len(page), "messages": page,
                     "@extra": extra}]
        case "parseMarkdown":
            return [{**req["text"], "@extra": extra}]
        case "sendMessage":
            sent = {**comment(504), "is_outgoing": True}
            return [{"@type": "updateNewMessage", "message": sent}, {**sent, "@extra": extra}]
    return [ok(req)]


class CommentsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        qt_app()
        from tgclient.models.messages import MessageListModel, Role

        self.Role = Role
        self.lib = FakeLib(responder)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        for event in (new_chat(CHANNEL, "News", 5, "chatTypeSupergroup", is_channel=True),
                      new_chat(GROUP, "News chat", 0, "chatTypeSupergroup")):
            self.lib.push(event)
        await wait_until(lambda: GROUP in self.chats.chats)
        self.model = MessageListModel(self.client, self.chats, self.users)

    def sent(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.lib.sent if r["@type"] == kind]

    async def test_comments_thread(self) -> None:
        self.model.open(CHANNEL)
        await wait_until(lambda: self.model.rowOf(20) >= 0)
        role = lambda mid: self.model.data(self.model.index(self.model.rowOf(mid)),  # noqa
                                           self.Role.Comments)
        self.assertEqual((role(20), role(21)), (3, -1))

        unread: list[int] = []
        self.model.unreadReady.connect(unread.append)
        self.model.openComments(20)
        await wait_until(lambda: self.model.threadMode and self.model.rowCount() == 4)
        self.assertEqual(self.model.chatId, GROUP)
        self.assertEqual(self.model.threadChannelTitle, "News")
        await wait_until(lambda: bool(unread))
        self.assertEqual(unread, [502])
        self.assertEqual(self.model.topic_obj(),
                         {"@type": "messageTopicThread", "message_thread_id": THREAD})
        self.model.sendMessage("nice", 0, {})
        await wait_until(lambda: self.model.rowOf(504) >= 0)
        self.assertEqual(self.sent("sendMessage")[0]["topic_id"]["message_thread_id"], THREAD)

        rows: list[int] = []
        self.model.jumpReady.connect(rows.append)
        self.model.closeComments()
        self.assertFalse(self.model.threadMode)
        self.assertEqual(self.model.chatId, CHANNEL)
        await wait_until(lambda: bool(rows))
        self.assertEqual(rows[-1], self.model.rowOf(20))


if __name__ == "__main__":
    unittest.main()
