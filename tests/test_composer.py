"""ComposerModel: cloud drafts, typing actions, staged attachments, pasted images; chat list
draft/typing/online roles and the chat picker."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from fakes import FakeLib, new_chat, ok, qt_app, wait_until

from tgclient.store.chats import ChatStore, draft_reply_to, draft_text
from tgclient.store.presence import PresenceStore
from tgclient.store.users import UserStore
from tgclient.td import TdHub


def draft(text: str, reply_to: int = 0) -> dict[str, Any]:
    return {"@type": "draftMessage", "date": 1,
            "reply_to": {"@type": "inputMessageReplyToMessage", "message_id": reply_to}
            if reply_to else None,
            "content": {"@type": "draftMessageContentText", "text": {"text": text,
                                                                     "entities": []}}}


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    match req["@type"]:
        case "setChatDraftMessage":  # TDLib echoes the draft back as an update
            return [{"@type": "updateChatDraftMessage", "chat_id": req["chat_id"],
                     "draft_message": req["draft_message"], "positions": []}, ok(req)]
        case "getChatHistory":
            return [{"@type": "messages", "total_count": 0, "messages": [],
                     "@extra": req["@extra"]}]
        case "parseMarkdown":
            return [{**req["text"], "@extra": req["@extra"]}]
        case "sendMessage":
            return [{"@type": "message", "id": 900, "chat_id": req["chat_id"], "date": 1,
                     "is_outgoing": True, "content": {"@type": "messageText", "text": {
                         "text": "", "entities": []}}, "@extra": req["@extra"]}]
    return [ok(req)]


class ComposerCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        qt_app()
        from tgclient.models.composer import ComposerModel
        from tgclient.models.messages import MessageListModel

        self.lib = FakeLib(responder)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.presence = PresenceStore(self.client)
        await self.push(new_chat(1, "Olena", 20, draft_message=draft("half a thought", 3)),
                        new_chat(2, "Petr", 10))
        self.messages = MessageListModel(self.client, self.chats, self.users)
        self.paste_dir = Path(tempfile.mkdtemp())
        self.composer = ComposerModel(self.client, self.chats, self.messages, self.paste_dir)
        patcher = mock.patch("tgclient.models.composer.DRAFT_DELAY", 0.05)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def push(self, *events: dict[str, Any]) -> None:
        seen: list[Any] = []
        off = self.client.on("updateTestMarker", seen.append)
        for event in (*events, {"@type": "updateTestMarker"}):
            self.lib.push(event)
        await wait_until(lambda: bool(seen))
        off()

    def sent(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.lib.sent if r["@type"] == kind]


class DraftTest(ComposerCase):
    async def test_draft_helpers_and_store(self) -> None:
        chat = self.chats.chats[1]
        self.assertEqual((draft_text(chat.draft), draft_reply_to(chat.draft)),
                         ("half a thought", 3))
        older = {"input_message_text": {"text": {"text": "old api"}}, "reply_to_message_id": 4}
        self.assertEqual((draft_text(older), draft_reply_to(older)), ("old api", 4))
        await self.push({"@type": "updateChatDraftMessage", "chat_id": 1, "draft_message": None,
                         "positions": []})
        self.assertIsNone(self.chats.chats[1].draft)

    async def test_open_chat_exposes_draft(self) -> None:
        self.messages.open(1)
        self.assertEqual((self.composer.draftText, self.composer.draftReplyTo),
                         ("half a thought", 3))
        self.composer.setDraft("half a thought", 3)  # QML loading it: nothing to save
        await wait_until(lambda: True)
        self.assertEqual(self.sent("setChatDraftMessage"), [])
        self.assertEqual(self.sent("sendChatAction"), [])

    async def test_typing_saves_after_a_pause_and_sends_typing(self) -> None:
        self.messages.open(2)
        self.composer.setDraft("h", 0)
        self.composer.setDraft("he", 0)
        self.composer.setDraft("hello", 0)
        await wait_until(lambda: bool(self.sent("setChatDraftMessage")))
        saves = self.sent("setChatDraftMessage")
        self.assertEqual(len(saves), 1)  # debounced
        content = saves[0]["draft_message"]["content"]
        self.assertEqual(content["@type"], "draftMessageContentText")
        self.assertEqual(content["text"]["text"], "hello")
        actions = self.sent("sendChatAction")
        self.assertEqual(len(actions), 1)  # throttled
        self.assertEqual(actions[0]["action"], {"@type": "chatActionTyping"})

    async def test_switching_chats_flushes_and_clearing_deletes(self) -> None:
        self.messages.open(2)
        self.composer.setDraft("unsent", 0)
        self.messages.open(1)  # before the pause ends
        await wait_until(lambda: bool(self.sent("setChatDraftMessage")))
        save = self.sent("setChatDraftMessage")[0]
        self.assertEqual((save["chat_id"], save["draft_message"]["content"]["text"]["text"]),
                         (2, "unsent"))
        self.composer.setDraft("", 0)  # chat 1: the user deleted its draft
        await wait_until(lambda: len(self.sent("setChatDraftMessage")) == 2)
        self.assertIsNone(self.sent("setChatDraftMessage")[1]["draft_message"])

    async def test_sent_cancels_pending_save(self) -> None:
        self.messages.open(2)
        self.composer.setDraft("message", 0)
        self.messages.send("message", 0)
        self.composer.setDraft("", 0)
        self.composer.sent()
        await wait_until(lambda: bool(self.sent("sendMessage")))
        await self.push({"@type": "updateTestMarker"})
        self.assertEqual(self.sent("setChatDraftMessage"), [])

    async def test_remote_draft_replaces_untouched_input_only(self) -> None:
        remote: list[tuple[str, Any]] = []
        self.composer.remoteDraft.connect(lambda text, reply: remote.append((text, reply)))
        self.messages.open(2)
        await self.push({"@type": "updateChatDraftMessage", "chat_id": 2,
                         "draft_message": draft("from phone"), "positions": []})
        self.assertEqual(remote, [("from phone", 0)])
        self.composer.setDraft("from phone, and typing here", 0)
        await self.push({"@type": "updateChatDraftMessage", "chat_id": 2,
                         "draft_message": draft("phone again"), "positions": []})
        self.assertEqual(len(remote), 1)  # the local edit wins


class AttachmentTest(ComposerCase):
    async def test_stage_and_send_with_caption(self) -> None:
        folder = Path(tempfile.mkdtemp())
        photo, doc = folder / "shot.png", folder / "notes.pdf"
        photo.write_bytes(b"png")
        doc.write_bytes(b"%PDF" * 100)
        self.messages.open(2)
        self.composer.stage([str(photo), str(doc), str(photo), "/nonexistent"])
        staged = self.composer.staged
        self.assertEqual([(f["name"], f["isImage"]) for f in staged],
                         [("shot.png", True), ("notes.pdf", False)])
        self.assertEqual(staged[1]["size"], "400 B")
        self.composer.sendStaged("look", 0)
        self.assertEqual(self.composer.staged, [])
        await wait_until(lambda: len(self.sent("sendMessage")) == 2)
        first, second = (r["input_message_content"] for r in self.sent("sendMessage"))
        self.assertEqual(first["@type"], "inputMessagePhoto")
        self.assertEqual(first["caption"]["text"], "look")
        self.assertNotIn("caption", second)

    async def test_paste_image_and_text(self) -> None:
        from PySide6.QtGui import QColor, QGuiApplication, QImage

        self.messages.open(2)
        QGuiApplication.clipboard().setText("just text")
        self.assertFalse(self.composer.pasteClipboard())
        image = QImage(20, 10, QImage.Format.Format_ARGB32)
        image.fill(QColor("#0E7C66"))
        QGuiApplication.clipboard().setImage(image)
        self.assertTrue(self.composer.pasteClipboard())
        staged = self.composer.staged
        self.assertEqual(len(staged), 1)
        self.assertTrue(staged[0]["isImage"])
        self.assertTrue(staged[0]["path"].startswith(str(self.paste_dir)))
        self.assertEqual(QImage(staged[0]["path"]).width(), 20)
        self.messages.open(1)  # switching chats drops what was staged
        self.assertEqual(self.composer.staged, [])

    async def test_old_pastes_are_cleaned(self) -> None:
        from tgclient.models.composer import ComposerModel

        old = self.paste_dir / "pasted-old.png"
        old.write_bytes(b"x")
        os.utime(old, (1, 1))
        fresh = self.paste_dir / "pasted-new.png"
        fresh.write_bytes(b"x")
        ComposerModel(self.client, self.chats, self.messages, self.paste_dir)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


class ChatListRolesTest(ComposerCase):
    async def test_draft_typing_online_and_picker(self) -> None:
        import time

        from PySide6.QtTest import QAbstractItemModelTester

        from tgclient.models.chat_list import ChatListModel, Role
        from tgclient.models.chat_picker import ChatPickerModel
        from tgclient.models.chat_picker import Role as PickerRole

        model = ChatListModel(self.chats, self.users, self.presence)
        QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
        model.setList("main")

        def role(chat_id: int, r: Any) -> Any:
            row = next(i for i in range(model.rowCount())
                       if model.data(model.index(i), Role.ChatId) == chat_id)
            return model.data(model.index(row), r)

        self.assertEqual(role(1, Role.Draft), "half a thought")
        self.assertEqual(role(2, Role.Draft), "")
        changed: list[Any] = []
        model.dataChanged.connect(lambda a, b, roles: changed.append(list(roles)))
        await self.push({"@type": "updateChatAction", "chat_id": 2, "topic_id": None,
                         "sender_id": {"@type": "messageSenderUser", "user_id": 2},
                         "action": {"@type": "chatActionTyping"}},
                        {"@type": "updateUserStatus", "user_id": 1, "status": {
                            "@type": "userStatusOnline", "expires": int(time.time()) + 60}})
        self.assertEqual(role(2, Role.Typing), "typing…")
        self.assertTrue(role(1, Role.Online))
        self.assertFalse(role(2, Role.Online))
        self.assertIn([Role.Typing], changed)
        self.assertIn([Role.Online], changed)

        picker = ChatPickerModel(self.chats, self.users)
        QAbstractItemModelTester(picker, QAbstractItemModelTester.FailureReportingMode.Fatal)
        picker.refresh()
        self.assertEqual(picker.rowCount(), 2)
        picker.setProperty("filter", "pet")
        self.assertEqual(picker.rowCount(), 1)
        self.assertEqual(picker.data(picker.index(0), PickerRole.ChatId), 2)


if __name__ == "__main__":
    unittest.main()
