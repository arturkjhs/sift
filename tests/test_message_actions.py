"""M7 in MessageListModel: unread separator, reactions, forwards, edit/delete/forward, status."""

from __future__ import annotations

import time
import unittest
from typing import Any

from fakes import new_chat, ok, qt_app, wait_until
from test_history import CHAT, HistoryCase, Server, msg

from tgclient.store.presence import PresenceStore


class ActionServer(Server):
    def __call__(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        extra = req.get("@extra")
        match req["@type"]:
            case "getMessageProperties":
                out = req["message_id"] >= 10_000
                return [{"@type": "messageProperties", "can_be_edited": out,
                         "can_be_deleted_only_for_self": True,
                         "can_be_deleted_for_all_users": out, "can_be_forwarded": True,
                         "can_be_replied": True, "@extra": extra}]
            case "getMessageAvailableReactions":
                return [{"@type": "availableReactions", "@extra": extra, "top_reactions": [
                    {"type": {"@type": "reactionTypeEmoji", "emoji": "\U0001F44D"}},
                    {"type": {"@type": "reactionTypeCustomEmoji", "custom_emoji_id": "9"}},
                    {"type": {"@type": "reactionTypeEmoji", "emoji": "\U0001F389"},
                     "needs_premium": True},
                    {"type": {"@type": "reactionTypeEmoji", "emoji": "❤️"}},
                ], "recent_reactions": [], "popular_reactions": []}]
            case "getMarkdownText":
                return [{"@type": "formattedText", "text": "**bold** text", "entities": [],
                         "@extra": extra}]
            case "editMessageText":
                text = req["input_message_content"]["text"]["text"]
                return [{**msg(req["message_id"], text, out=True, edit_date=1), "@extra": extra}]
            case "addMessageReaction":
                info = {"@type": "messageInteractionInfo", "reactions": {
                    "@type": "messageReactions", "are_tags": False, "reactions": [
                        {"type": req["reaction_type"], "total_count": 1, "is_chosen": True}]}}
                return [{"@type": "updateMessageInteractionInfo", "chat_id": req["chat_id"],
                         "message_id": req["message_id"], "interaction_info": info}, ok(req)]
        return super().__call__(req)


class ActionCase(HistoryCase):
    total = 6

    async def asyncSetUp(self) -> None:
        self.server = ActionServer(self.total)
        from fakes import FakeLib

        from tgclient.store.chats import ChatStore
        from tgclient.store.users import UserStore
        from tgclient.td import TdHub

        self.lib = FakeLib(self.server)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.presence = PresenceStore(self.client)
        qt_app()
        from PySide6.QtTest import QAbstractItemModelTester

        from tgclient.models.messages import MessageListModel, Role

        self.Role = Role
        await self.push(
            new_chat(CHAT, "Friends", 1, "chatTypeSupergroup", **self.chat_fields()),
            {"@type": "updateUser", "user": {"id": 5, "first_name": "Olena", "last_name": "K"}},
            {"@type": "updateUser", "user": {"id": 6, "first_name": "Petr", "last_name": ""}},
        )
        self.model = MessageListModel(self.client, self.chats, self.users, None, self.presence)
        self.tester = QAbstractItemModelTester(
            self.model, QAbstractItemModelTester.FailureReportingMode.Fatal)

    def chat_fields(self) -> dict[str, Any]:
        return {}

    def role(self, row: int, role: Any) -> Any:
        return self.model.data(self.model.index(row), role)

    def sent(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.lib.sent if r["@type"] == kind]

    async def open_loaded(self, rows: int | None = None) -> None:
        self.model.open(CHAT)
        await wait_until(lambda: not self.model.loading
                         and self.model.rowCount() == (rows or self.total))


class MessageActionsTest(ActionCase):
    async def test_actions_for_own_and_incoming_messages(self) -> None:
        await self.open_loaded()
        await self.push({"@type": "updateNewMessage", "message": msg(10_000, "mine", out=True)})
        got: list[tuple[Any, dict[str, Any]]] = []
        self.model.actionsReady.connect(lambda mid, actions: got.append((mid, actions)))
        self.model.requestActions(10_000)
        self.model.requestActions(6)
        await wait_until(lambda: len(got) == 2)
        actions = dict(got)
        self.assertTrue(actions[10_000]["canEdit"])
        self.assertTrue(actions[10_000]["canDeleteForAll"])
        self.assertFalse(actions[6]["canEdit"])
        self.assertFalse(actions[6]["canDeleteForAll"])
        self.assertTrue(actions[6]["canDelete"])
        self.assertTrue(actions[6]["canCopy"])
        # custom and premium-only reactions aren't offered
        self.assertEqual([r["key"] for r in actions[6]["reactions"]],
                         ["\U0001F44D", "\u2764\ufe0f"])
        self.assertEqual(actions[6]["allReactions"], actions[6]["reactions"])  # nothing more
        from tgclient.store.reactions import DEFAULT_REACTIONS, QUICK, display

        self.assertEqual(display("\u2764"), "\u2764\ufe0f")  # colour, not a black glyph
        self.assertEqual(display("\u2764\u200d\U0001F525"), "\u2764\ufe0f\u200d\U0001F525")
        self.assertEqual(display("\U0001F44D"), "\U0001F44D")
        self.assertIn("\u2764", DEFAULT_REACTIONS)  # TDLib's spelling, without U+FE0F
        self.assertGreater(len(DEFAULT_REACTIONS), QUICK)

    async def test_reactions_role_and_toggle(self) -> None:
        await self.open_loaded()
        self.assertEqual(self.role(0, self.Role.Reactions), [])
        self.model.toggleReaction(6, "\U0001F44D")
        await wait_until(lambda: self.role(0, self.Role.Reactions) != [])
        self.assertEqual(self.role(0, self.Role.Reactions), [
            {"key": "\U0001F44D", "label": "\U0001F44D", "count": 1, "chosen": True,
             "image": ""}])
        added = self.sent("addMessageReaction")[0]
        self.assertEqual(added["reaction_type"],
                         {"@type": "reactionTypeEmoji", "emoji": "\U0001F44D"})
        self.model.toggleReaction(6, "\U0001F44D")  # chosen: removes
        await wait_until(lambda: bool(self.sent("removeMessageReaction")))

    async def test_forwarded_from(self) -> None:
        await self.open_loaded()
        forwarded = msg(7, "fw", forward_info={"origin": {
            "@type": "messageOriginUser", "sender_user_id": 6}, "date": 1})
        hidden = msg(8, "fw", forward_info={"origin": {
            "@type": "messageOriginHiddenUser", "sender_name": "Anon"}, "date": 1})
        await self.push({"@type": "updateNewMessage", "message": forwarded},
                        {"@type": "updateNewMessage", "message": hidden})
        self.assertEqual(self.role(0, self.Role.ForwardedFrom), "Anon")
        self.assertEqual(self.role(1, self.Role.ForwardedFrom), "Petr")
        self.assertEqual(self.role(2, self.Role.ForwardedFrom), "")

    async def test_edit_round_trip(self) -> None:
        await self.open_loaded()
        await self.push({"@type": "updateNewMessage", "message": {
            **msg(10_000, "bold text", out=True), "content": {"@type": "messageText", "text": {
                "text": "bold text", "entities": [{"offset": 0, "length": 4, "type": {
                    "@type": "textEntityTypeBold"}}]}}}})
        self.assertEqual(self.model.lastEditableId(), 10_000)
        edits: list[tuple[Any, str]] = []
        self.model.editReady.connect(lambda mid, text: edits.append((mid, text)))
        self.model.startEdit(10_000)
        await wait_until(lambda: bool(edits))
        self.assertEqual(edits, [(10_000, "**bold** text")])
        self.model.saveEdit(10_000, "**bold** text, edited")
        await wait_until(lambda: bool(self.role(0, self.Role.Edited)))
        request = self.sent("editMessageText")[0]
        self.assertEqual(request["input_message_content"]["@type"], "inputMessageText")
        self.assertIn("edited", self.role(0, self.Role.Html))

    async def test_edit_caption(self) -> None:
        await self.open_loaded()
        photo = {**msg(10_001, out=True), "content": {
            "@type": "messagePhoto", "photo": {"sizes": []}, "caption": {"text": "", "entities": []}}}
        await self.push({"@type": "updateNewMessage", "message": photo})
        self.model.saveEdit(10_001, "a caption")
        await wait_until(lambda: bool(self.sent("editMessageCaption")))
        self.assertEqual(self.sent("editMessageCaption")[0]["caption"]["text"], "a caption")

    async def test_delete_and_forward(self) -> None:
        await self.open_loaded()
        self.model.deleteMessage(6, True)
        self.model.forward(5, 777)
        await wait_until(lambda: bool(self.sent("deleteMessages") and self.sent("forwardMessages")))
        delete = self.sent("deleteMessages")[0]
        self.assertEqual((delete["chat_id"], delete["message_ids"], delete["revoke"]),
                         (CHAT, [6], True))
        forward = self.sent("forwardMessages")[0]
        self.assertEqual((forward["chat_id"], forward["from_chat_id"], forward["message_ids"]),
                         (777, CHAT, [5]))

    async def test_send_text_clears_cloud_draft_and_file_caption(self) -> None:
        await self.open_loaded()
        self.model.send("hi", 0)
        self.model.send_file("/tmp/x.pdf", "**see** this", 4)
        await wait_until(lambda: len(self.sent("sendMessage")) == 2)
        text, document = self.sent("sendMessage")
        self.assertTrue(text["input_message_content"]["clear_draft"])
        content = document["input_message_content"]
        self.assertEqual(content["@type"], "inputMessageDocument")
        self.assertEqual(content["caption"]["text"], "**see** this")  # fake parseMarkdown echoes
        self.assertEqual(document["reply_to"]["message_id"], 4)

    async def test_group_status_and_typing(self) -> None:
        await self.push({"@type": "updateChatAction", "chat_id": CHAT, "topic_id": None,
                         "sender_id": {"@type": "messageSenderUser", "user_id": 5},
                         "action": {"@type": "chatActionTyping"}})
        await self.open_loaded()
        self.assertEqual(self.model.chatStatus, "Olena is typing…")
        self.assertTrue(self.model.chatStatusActive)
        changes: list[int] = []
        self.model.statusChanged.connect(lambda: changes.append(1))
        await self.push({"@type": "updateChatAction", "chat_id": CHAT, "topic_id": None,
                         "sender_id": {"@type": "messageSenderUser", "user_id": 5},
                         "action": {"@type": "chatActionCancel"}})
        self.assertTrue(changes)
        self.assertEqual(self.model.chatStatus, "")  # member count not known yet


class PrivateStatusTest(ActionCase):
    async def test_private_chat_shows_last_seen(self) -> None:
        await self.push(new_chat(5, "Olena", 2, "chatTypePrivate"))
        await self.push({"@type": "updateUserStatus", "user_id": 5, "status": {
            "@type": "userStatusOnline", "expires": int(time.time()) + 60}})
        self.model.open(5)
        self.assertEqual(self.model.chatStatus, "online")
        self.assertTrue(self.model.chatStatusActive)
        await self.push({"@type": "updateUserStatus", "user_id": 5,
                         "status": {"@type": "userStatusRecently"}})
        self.assertEqual(self.model.chatStatus, "last seen recently")
        self.assertFalse(self.model.chatStatusActive)


class UnreadSeparatorTest(ActionCase):
    total = 120

    def chat_fields(self) -> dict[str, Any]:
        return {"unread_count": 20, "last_read_inbox_message_id": 100}

    async def test_opens_at_first_unread(self) -> None:
        ids: list[int] = []
        self.model.unreadReady.connect(ids.append)
        self.model.open(CHAT)
        await wait_until(lambda: bool(ids))
        self.assertEqual(ids, [101])
        row = self.model.rowOf(101)
        self.assertTrue(self.role(row, self.Role.UnreadSeparator))
        self.assertFalse(self.role(row - 1, self.Role.UnreadSeparator))
        self.model.send("reply", 0)  # answering hides the separator
        self.assertFalse(self.role(self.model.rowOf(101), self.Role.UnreadSeparator))


class FarUnreadTest(ActionCase):
    total = 500

    def chat_fields(self) -> dict[str, Any]:
        return {"unread_count": 495, "last_read_inbox_message_id": 5}

    async def test_too_far_back_opens_at_the_bottom(self) -> None:
        rows: list[int] = []
        self.model.unreadReady.connect(rows.append)
        self.model.open(CHAT)
        await wait_until(lambda: not self.model.loading and self.model.rowCount() > 50)
        await wait_until(lambda: not self.model._history.loading)
        self.assertEqual(rows, [])
        self.assertGreater(self.model._history.messages[-1]["id"], 5)


if __name__ == "__main__":
    unittest.main()
