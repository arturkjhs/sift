"""Loads the real QML offscreen with a fake TDLib: catches QML errors and wiring mistakes."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fakes import (
    FakeEmbedder,
    FakeLib,
    FakeRouter,
    auth_state,
    error,
    history_ids,
    new_chat,
    ok,
    qt_app,
    wait_until,
)

from tgclient.config import Settings
from tgclient.store.reactions import DEFAULT_REACTIONS

VOICE_PATH = os.path.join(tempfile.gettempdir(), "tgc-test-voice.ogg")
STICKER_COLORS = {301: "#E8A33D", 302: "#3FB295", 303: "#D4695B", 304: "#6C7F99"}


def _sticker_path(file_id: int) -> str:
    return os.path.join(tempfile.gettempdir(), f"tgc-test-sticker-{file_id}.png")


def _sticker(file_id: int, emoji: str) -> dict[str, Any]:
    return {"@type": "sticker", "id": str(file_id), "set_id": "77", "width": 512,
            "height": 512, "emoji": emoji, "format": {"@type": "stickerFormatWebp"},
            "sticker": _file(file_id), "thumbnail": None}


LONG_CHAT = 4
PHOTO_PATH = os.path.join(tempfile.gettempdir(), "tgc-test-photo.png")


def _long_history(req: dict[str, Any]) -> list[dict[str, Any]]:
    """Chat 4: 300 messages, read up to 40 (opens on the separator, not at the bottom)."""
    ids = history_ids(list(range(1, 301)), req)
    messages = [{**_msg(i, f"Message number {i} " + "word " * (i % 7)), "chat_id": LONG_CHAT,
                 "date": 1_700_000_000 + i * 60} for i in ids]
    return [{"@type": "messages", "total_count": len(ids), "messages": messages,
             "@extra": req["@extra"]}]


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    match req["@type"]:
        case "getChatHistory" if req["chat_id"] == LONG_CHAT:
            return _long_history(req)
        case "getMessageProperties":
            return [{"@type": "messageProperties", "can_be_edited": True,
                     "can_be_deleted_only_for_self": True, "can_be_deleted_for_all_users": True,
                     "can_be_forwarded": True, "can_be_replied": True, "@extra": req["@extra"]}]
        case "getMessageAvailableReactions":
            return [{"@type": "availableReactions", "@extra": req["@extra"], "top_reactions": [
                {"type": {"@type": "reactionTypeEmoji", "emoji": e}}
                for e in ("\U0001F44D", "\u2764", "\U0001F602", "\U0001F525")],
                "recent_reactions": [], "popular_reactions": [
                    {"type": {"@type": "reactionTypeEmoji", "emoji": e}}
                    for e in DEFAULT_REACTIONS]}]
        case "sendMessage" if req["input_message_content"]["@type"] == "inputMessagePhoto":
            sent = {**_media(8, {"@type": "messagePhoto", "photo": {"sizes": []},
                                 "caption": req["input_message_content"].get("caption")}),
                    "is_outgoing": True}
            return [{"@type": "updateNewMessage", "message": sent},
                    {**sent, "@extra": req["@extra"]}]
        case "getMessage" if req["message_id"] == 5:
            return [{**_media(5, {"@type": "messageVoiceNote", "voice_note": {
                "duration": 3, "waveform": "", "mime_type": "audio/ogg", "voice": _file(51)}}),
                "@extra": req["@extra"]}]
        case "getMessage" if req["chat_id"] == 1 and req["message_id"] in (1, 2, 3):
            texts = {1: "Hi!", 2: "A longer message that should wrap inside the bubble.",
                     3: "See you at the office"}
            return [{**_msg(req["message_id"], texts[req["message_id"]]), "@extra": req["@extra"]}]
        case "downloadFile" if req["file_id"] in STICKER_COLORS:
            return [{**_file(req["file_id"]), "local": {
                "path": _sticker_path(req["file_id"]), "is_downloading_completed": True},
                "@extra": req["@extra"]}]
        case "getInstalledStickerSets":
            return [{"@type": "stickerSets", "total_count": 1, "@extra": req["@extra"], "sets": [
                {"@type": "stickerSetInfo", "id": "77", "title": "Shapes", "name": "shapes",
                 "is_archived": False, "covers": [_sticker(301, "\U0001F600")]}]}]
        case "getRecentStickers":
            return [{"@type": "stickers", "stickers": [_sticker(302, "\U0001F44D")],
                     "@extra": req["@extra"]}]
        case "getStickerSet":
            return [{"@type": "stickerSet", "id": "77", "title": "Shapes", "@extra": req["@extra"],
                     "stickers": [_sticker(i, "\U0001F600") for i in STICKER_COLORS]}]
        case "sendMessage" if req["input_message_content"]["@type"] == "inputMessageSticker":
            sent = {**_media(7, {"@type": "messageSticker", "sticker": _sticker(302, "x")}),
                    "is_outgoing": True}
            return [{"@type": "updateNewMessage", "message": sent},
                    {**sent, "@extra": req["@extra"]}]
        case "downloadFile" if req["file_id"] == 51:
            return [{**_file(51), "local": {"path": VOICE_PATH, "is_downloading_completed": True},
                     "@extra": req["@extra"]}]
        case "getOption":
            return [auth_state("authorizationStateWaitTdlibParameters"),
                    {"@type": "optionValueString", "value": "test", "@extra": req["@extra"]}]
        case "setTdlibParameters":
            return [auth_state("authorizationStateWaitPhoneNumber"), ok(req)]
        case "setAuthenticationPhoneNumber":
            return [auth_state("authorizationStateWaitCode", code_info={
                "type": {"@type": "authenticationCodeTypeSms"}}), ok(req)]
        case "checkAuthenticationCode":
            if req["code"] != "11111":
                return [error(req, 400, "PHONE_CODE_INVALID")]
            return [auth_state("authorizationStateReady"),
                    new_chat(1, "Olena", 300, unread_count=2),
                    new_chat(2, "Prague IT", 200, "chatTypeSupergroup"),
                    new_chat(LONG_CHAT, "Book club", 100, "chatTypeSupergroup",
                             unread_count=260, last_read_inbox_message_id=40),
                    ok(req)]
        case "loadChats":
            return [error(req, 404, "Not Found")]
        case "getChatHistory":
            if req["from_message_id"] != 0:
                return [{"@type": "messages", "total_count": 0, "messages": [],
                         "@extra": req["@extra"]}]
            return [{"@type": "messages", "total_count": 5, "@extra": req["@extra"], "messages": [
                {**_media(5, {"@type": "messageVoiceNote", "voice_note": {
                    "duration": 3, "waveform": "", "mime_type": "audio/ogg", "voice": _file(51)}}),
                 "reply_to": {"@type": "messageReplyToMessage", "chat_id": 1,
                              "message_id": 3}},  # to my message: "for you" in the summary
                _media(4, {"@type": "messageDocument", "document": {
                    "file_name": "a.pdf", "mime_type": "application/pdf", "document": _file(52)}}),
                _msg(3, "See **you** at https://example.com 🚀", out=True),
                _msg(2, "A longer message that should wrap inside the bubble because it is "
                        "definitely wider than the maximum bubble width allows.", reply_to=1),
                _msg(1, "Hi!"),
            ]}]
        case "parseMarkdown":
            return [{**req["text"], "@extra": req["@extra"]}]
        case "searchMessages":
            return [{"@type": "foundMessages", "total_count": 1, "next_offset": "",
                     "@extra": req["@extra"], "messages": [
                         _msg(2, "A longer message that should wrap inside the bubble because "
                                 "it is definitely wider than the maximum bubble width allows.")]}]
        case "getMessages":
            return [{"@type": "messages", "total_count": 0, "messages": [],
                     "@extra": req["@extra"]}]
        case "sendMessage":
            sent = _msg(6, req["input_message_content"]["text"]["text"], out=True)
            return [{"@type": "updateNewMessage", "message": sent},
                    {**sent, "@extra": req["@extra"]}]
        case "close":
            return [ok(req), auth_state("authorizationStateClosed")]
    return [ok(req)]


def _file(fid: int) -> dict[str, Any]:
    return {"@type": "file", "id": fid, "size": 1000, "expected_size": 1000,
            "local": {"path": "", "is_downloading_completed": False}, "remote": {}}


def _media(mid: int, content: dict[str, Any]) -> dict[str, Any]:
    return {**_msg(mid, ""), "content": content}


def _msg(mid: int, text: str, out: bool = False, reply_to: int = 0) -> dict[str, Any]:
    message: dict[str, Any] = {
        "@type": "message", "id": mid, "chat_id": 1, "is_outgoing": out, "date": 1_700_000_000 + mid,
        "sender_id": {"@type": "messageSenderUser", "user_id": 99 if out else 5},
        "content": {"@type": "messageText", "text": {"text": text, "entities": []}},
    }
    if reply_to:
        message["reply_to"] = {"@type": "messageReplyToMessage", "chat_id": 1,
                               "message_id": reply_to}
    return message


class QmlSmokeTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with open(VOICE_PATH, "wb") as f:
            f.write(b"OggS")
        qt_app()
        from PySide6.QtGui import QColor as _QColor
        from PySide6.QtGui import QImage as _QImage

        photo = _QImage(320, 200, _QImage.Format.Format_RGB32)
        photo.fill(_QColor("#6C7F99"))
        photo.save(PHOTO_PATH)
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QImage, QPainter

        for file_id, color in STICKER_COLORS.items():  # transparent PNGs standing in for WebP
            image = QImage(256, 256, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(28, 28, 200, 200, 60, 60)
            painter.end()
            image.save(_sticker_path(file_id))

    async def test_login_then_chat_list(self) -> None:
        app = qt_app()
        from PySide6.QtQuick import QQuickItem

        from tgclient.app import Session, create_engine
        from tgclient.ui.shell import ShellController

        settings = Settings(api_id=1, api_hash="x", data_dir=Path("/tmp/tgc-test"),
                            use_test_dc=False, td_log_level=0, log_level="WARNING")
        from tgclient.services.ai_store import AiStore

        def ai_reply(body: dict[str, Any]) -> str:
            system = str(body["messages"][0]["content"])
            if "understand one message" in system:
                target = str(body["messages"][1]["content"]).rsplit(">>> [m", 1)[1][:1]
                if target == "1":
                    return ("KIND: light\nREACTIONS: 😂 ❤️ 🦄\nREPLY: Привет-привет 👋\n"
                            "## Суть\nПросто приветствие [m1].")
                if target == "3":
                    return "KIND: informational\n## Суть\nВстреча в офисе, ссылка [m3]."
                return ("KIND: actionable\n## Суть\nОлена спрашивает про ширину пузыря [m2].\n"
                        "## Чего хотят от тебя\nОтвета, подходит ли такой перенос строк.\n"
                        "## Неясно\nО каком экране речь.")
            if "draft replies" in system:
                return ("ANALYSIS: Просит оценить перенос строк в пузыре\n"
                        "### Согласиться\nYes, wrapping looks right to me.\n"
                        "TRANSLATION: Да, по-моему перенос выглядит правильно.\n"
                        "### Уточнить\nWhich screen size did you test?\n"
                        "TRANSLATION: На каком размере экрана проверяла?\n"
                        "### Перенести\nLet me check tonight and get back to you.\n"
                        "TRANSLATION: Посмотрю вечером и отвечу.")
            if "Translate" in system:
                return "Ahoj! Uvidíme se v pátek."
            if "extract agreed dates" in system:
                return ('{"events": [{"title": "Office meetup", "start": "2026-03-13T18:00", '
                        '"location": "Lucerna", "ref": "m3"}]}')
            if "answer a question" in system:
                return "At the office on Friday, see [m3]."
            if "digest of several" in system:
                return ("## Olena\n- Meeting at the office, link sent [m1]\n\n"
                        "Nothing important: Book club")
            return ("## For you\n- Olena answers you with a voice message [m5].\n"
                    "- Olena asks everyone whether the bubble wraps right [m2].\n\n"
                    "**Office meetup.** Agreed to meet at the office; link sent [m3].\n\n"
                    "**Bubble width.** Long messages wrap correctly [m2, m1].")

        router = FakeRouter(ai_reply, cost=0.0031)
        from tgclient.services.search_index import SearchIndex

        session = Session(settings, lib=FakeLib(responder), ai_store=AiStore(":memory:"),
                          router=router.client(), search_index=SearchIndex(":memory:"),
                          embedder=FakeEmbedder())
        shell = ShellController(asyncio.Event())

        warnings: list[str] = []
        engine = create_engine(session, shell, on_warnings=warnings.extend)
        self.assertTrue(engine.rootObjects(), f"QML failed to load: {warnings}")
        window = engine.rootObjects()[0]

        def pump() -> None:
            for _ in range(5):
                app.processEvents()

        async def settle(seconds: float = 0.3) -> None:
            """Let Qt run animations (popup transitions) for a while."""
            for _ in range(int(seconds / 0.02)):
                pump()
                await asyncio.sleep(0.02)

        start = asyncio.ensure_future(session.start())

        async def step_is(step: str) -> None:
            async def poll() -> bool:
                pump()
                return session.auth.step == step
            deadline = asyncio.get_running_loop().time() + 3
            while not await poll():
                if asyncio.get_running_loop().time() > deadline:
                    self.fail(f"step stayed {session.auth.step!r}, expected {step!r}")
                await asyncio.sleep(0.02)

        await step_is("phone")
        session.auth.submit("+420123456789")
        await step_is("code")
        session.auth.submit("00000")
        await wait_until(lambda: (pump(), session.auth.error)[1] != "" and not session.auth.busy)
        self.assertIn("Wrong code", session.auth.error)
        session.auth.submit("11111")
        await step_is("ready")
        await start

        await wait_until(lambda: (pump(), session.chat_list.rowCount())[1] == 3)
        pump()

        def find_list_views(item: QQuickItem) -> list[QQuickItem]:
            found = [item] if item.metaObject().className().startswith("QQuickListView") else []
            for child in item.childItems():
                found.extend(find_list_views(child))
            return found

        views = find_list_views(window.contentItem())
        counts = sorted(v.property("count") for v in views)
        self.assertIn(3, counts, f"chat list not rendered, ListView counts: {counts}")
        self.assertEqual(warnings, [], "QML warnings during run")

        # open a chat: messages render, sending adds a bubble, no binding loops
        main_view = window.findChild(QQuickItem, "mainView")
        main_view.setProperty("selectedChatId", 1)
        session.messages.open(1)
        await wait_until(lambda: (pump(), session.messages.rowCount())[1] == 5)
        pump()
        counts = sorted(v.property("count") for v in find_list_views(window.contentItem()))
        self.assertIn(5, counts, f"messages not rendered, ListView counts: {counts}")

        session.messages.send("hello", 0)
        await wait_until(lambda: (pump(), session.messages.rowCount())[1] == 6)
        pump()
        self.assertEqual(warnings, [], "QML warnings after opening a chat")

        # AI: off by default; consent dialog; summary panel with message links; transcript
        from PySide6.QtCore import Q_ARG, QMetaObject, QObject

        self.assertEqual(session.ai.chatId, 1)  # bound from QML
        self.assertFalse(session.ai.enabled)
        consent = window.findChild(QObject, "aiConsent")
        QMetaObject.invokeMethod(consent, "open")
        await settle()
        _screenshot(window, "consent")
        session.ai.setEnabled(True)
        QMetaObject.invokeMethod(consent, "close")
        pump()
        _screenshot(window, "transcribe-link")
        message_view = window.findChild(QQuickItem, "messageView")
        message_view.setProperty("summaryOpen", True)
        session.ai.summarize("unread")
        await wait_until(lambda: (pump(), session.ai.summaryState)[1] == "done")
        self.assertIn("tgc://message/3", session.ai.summaryText)
        session.ai.transcribe(5)
        await wait_until(lambda: (pump(), session.ai_service.transcript(1, 5).state)[1] == "done")
        pump()
        self.assertEqual(warnings, [], "QML warnings with AI on")
        _screenshot(window, "chat-ai")

        # Context menus: message and person
        for name, props in (("messageMenu", {"messageId": 3, "senderName": "Olena"}),
                            ("personMenu", {"senderKey": "user:5", "senderName": "Olena K",
                                            "messageId": 1, "initials": "OK"})):
            menu = window.findChild(QObject, name)
            for key, value in props.items():
                menu.setProperty(key, value)
            QMetaObject.invokeMethod(menu, "popup")
            await settle()
            self.assertTrue(menu.property("opened"), name)
            _screenshot(window, name)
            QMetaObject.invokeMethod(menu, "close")
            await settle(0.15)
        self.assertEqual(warnings, [], "QML warnings in menus")

        # Search: results replace the chat list; opening a message result flashes it
        session.search.setProperty("query", "longer")
        await wait_until(lambda: (pump(), session.search.rowCount())[1] >= 2
                         and not session.search.busy)
        results = window.findChild(QQuickItem, "searchResults")
        self.assertTrue(results.isVisible())
        pump()
        _screenshot(window, "search")
        QMetaObject.invokeMethod(main_view, "openChat", Q_ARG("QVariant", 1),
                                 Q_ARG("QVariant", 2))
        pump()
        self.assertEqual(message_view.property("highlightId"), 2)
        session.search.setProperty("query", "")
        pump()
        self.assertFalse(results.isVisible())
        self.assertEqual(warnings, [], "QML warnings in search")

        # Emoji and stickers: emoji go into the text, a sticker is sent
        picker = window.findChild(QObject, "emojiPicker")
        QMetaObject.invokeMethod(picker, "open")
        await settle()
        self.assertTrue(picker.property("opened"))
        session.emojis.setProperty("category", "smileys")
        await settle(0.1)
        _screenshot(window, "picker-emoji")
        QMetaObject.invokeMethod(picker, "emojiPicked", Q_ARG(str, "\U0001F44D"))
        def find_item(item: QQuickItem, name: str) -> QQuickItem | None:
            if item.objectName() == name:
                return item
            for child in item.childItems():
                if (found := find_item(child, name)) is not None:
                    return found
            return None

        composer_input = find_item(window.contentItem(), "composerInput")
        self.assertIn("\U0001F44D", composer_input.property("text"))
        picker.setProperty("tab", "stickers")
        await wait_until(lambda: (pump(), session.stickers.rowCount())[1] == 1)  # recent
        await settle()
        _screenshot(window, "picker-stickers")
        session.stickers.setProperty("currentSet", "77")
        await wait_until(lambda: (pump(), session.stickers.rowCount())[1] == 4)
        await settle()
        sticker = session.stickers.sticker(0)
        QMetaObject.invokeMethod(picker, "stickerPicked", Q_ARG("QVariant", sticker))
        await wait_until(lambda: (pump(), session.messages.rowCount())[1] == 7)
        await settle(0.2)
        self.assertFalse(picker.property("opened"))
        self.assertEqual(warnings, [], "QML warnings in the emoji/sticker picker")

        # --- M8: translation in the bubble, the AI panel, composer translation, digest ------
        session.ai.translate(2)
        await wait_until(lambda: (pump(), session.ai_service.translation(1, 2))[1] is not None
                         and session.ai_service.translation(1, 2).state == "done")
        session.ai.openPanel("ask")
        session.ai.ask("where do we meet?")
        await wait_until(lambda: (pump(), session.ai.summaryState)[1] == "done")
        await settle(0.1)
        self.assertIn("tgc://message/3", session.ai.summaryText)
        self.assertEqual(warnings, [], "QML warnings in the ask panel")
        _screenshot(window, "ai-ask-translation")
        from unittest import mock

        with mock.patch("tgclient.services.ai.time.time", return_value=1_700_000_100):
            session.ai.findEvents()  # the fake history is from 2023
            await wait_until(lambda: (pump(), session.ai.summaryState)[1] == "done")
        self.assertTrue(session.ai.hasEvents)
        await settle(0.1)
        _screenshot(window, "ai-events")
        summary_menu = window.findChild(QObject, "smartConsent")
        QMetaObject.invokeMethod(summary_menu, "open")
        await settle()
        _screenshot(window, "smart-consent")
        QMetaObject.invokeMethod(summary_menu, "close")
        await settle(0.15)
        session.ai.translateDraft("Привет! Увидимся в пятницу.", "cs")
        preview = find_item(window.contentItem(), "translationPreview")
        await wait_until(lambda: (pump(), preview.isVisible())[1])
        await settle(0.1)
        _screenshot(window, "composer-translation")
        assist_menu = window.findChild(QObject, "assistMenu")
        QMetaObject.invokeMethod(assist_menu, "popup")
        await settle()
        _screenshot(window, "assist-menu")
        QMetaObject.invokeMethod(assist_menu, "close")
        await settle(0.15)
        sent_texts = lambda: [r["input_message_content"].get("text", {}).get("text")
                              for r in session.client._hub._lib.sent
                              if r["@type"] == "sendMessage"]
        QMetaObject.invokeMethod(find_item(window.contentItem(), "sendTranslation"), "clicked")
        await wait_until(lambda: (pump(), "Ahoj! Uvidíme se v pátek." in sent_texts())[1])
        pump()
        self.assertFalse(preview.isVisible())
        session.ai_service.set_flag(1, "digest", True)
        digest_dialog = window.findChild(QObject, "digestDialog")
        QMetaObject.invokeMethod(digest_dialog, "open")
        with mock.patch("tgclient.services.ai.time.time", return_value=1_700_000_100):
            session.ai.digest()
            await wait_until(lambda: (pump(), session.ai.globalState)[1] == "done")
        await settle()
        self.assertIn("tgc://message/1/1", session.ai_service.summary(0, "digest").text)
        _screenshot(window, "digest")
        QMetaObject.invokeMethod(digest_dialog, "close")
        await settle(0.15)
        self.assertEqual(warnings, [], "QML warnings in M8 views")

        # Explain a message, then suggest replies and insert one (not sent)
        session.ai.explainMessage(2, "Olena")
        await wait_until(lambda: (pump(), session.ai.summaryState)[1] == "done")
        await settle(0.1)
        self.assertIn("tgc://message/2", session.ai.summaryText)
        _screenshot(window, "ai-explain")
        panel = window.findChild(QQuickItem, "summaryPanel")

        def shown(name: str) -> bool:
            item = find_item(panel, name)
            return item is not None and item.isVisible()

        self.assertTrue(shown("suggestFromExplain"))  # actionable: full reply options offered
        self.assertFalse(shown("quickReactions"))
        session.ai.explainMessage(3, "")
        await wait_until(lambda: (pump(), session.ai.summaryState)[1] == "done"
                         and session.ai.explainKind == "informational")
        await settle(0.1)
        self.assertFalse(shown("suggestFromExplain"))
        self.assertFalse(shown("quickReactions"))
        self.assertFalse(shown("quickReply"))
        # light: reactions the chat allows and a short reply, no analysis
        session.ai.explainMessage(1, "Olena")
        await wait_until(lambda: (pump(), session.ai.summaryState)[1] == "done"
                         and session.ai.explainKind == "light")
        await settle(0.1)
        self.assertTrue(shown("quickReactions"))
        self.assertTrue(shown("quickReply"))
        self.assertFalse(shown("suggestFromExplain"))
        _screenshot(window, "ai-explain-light")
        chips = [i for i in _all_items(panel) if i.objectName() == "quickReaction"]
        self.assertEqual([c.property("modelData")["key"] for c in chips],
                         ["\U0001F602", "\u2764"])  # 🦄 isn't allowed in this chat
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        center = chips[1].mapToScene(chips[1].boundingRect().center()).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(center.x(), center.y()))
        sent = session.client._hub._lib.sent
        await wait_until(lambda: (pump(), any(r["@type"] == "addMessageReaction"
                                              for r in sent))[1])
        reaction = next(r for r in sent if r["@type"] == "addMessageReaction")
        self.assertEqual((reaction["chat_id"], reaction["message_id"], reaction["reaction_type"],
                          reaction["is_big"]),
                         (1, 1, {"@type": "reactionTypeEmoji", "emoji": "\u2764"}, False))
        self.assertTrue(chips[1].property("sent"))
        session.ai.insertQuickReply()
        pump()
        self.assertEqual(message_view.property("replyToId"), 1)
        self.assertEqual(find_item(window.contentItem(), "composerInput").property("text"),
                         "Привет-привет 👋")
        message_view.setProperty("replyToId", 0)
        QMetaObject.invokeMethod(find_item(window.contentItem(), "composerInput"), "clear")
        self.assertEqual(warnings, [], "QML warnings in the explain panel kinds")
        session.ai.explainMessage(2, "Olena")  # cached: back to the actionable one
        await wait_until(lambda: (pump(), session.ai.explainKind)[1] == "actionable")
        session.ai.suggestReplies(2, "Olena")
        await wait_until(lambda: (pump(), session.ai.summaryState)[1] == "done")
        await settle(0.1)
        self.assertEqual(len(session.ai.replyOptions), 3)
        _screenshot(window, "ai-reply")
        session.ai.insertOption(1)
        pump()
        self.assertEqual(message_view.property("replyToId"), 2)
        composer_text = find_item(window.contentItem(), "composerInput").property("text")
        self.assertEqual(composer_text, "Which screen size did you test?")
        self.assertEqual(warnings, [], "QML warnings in explain / suggest reply")
        message_view.setProperty("replyToId", 0)
        QMetaObject.invokeMethod(find_item(window.contentItem(), "composerInput"), "clear")

        # --- M7 -------------------------------------------------------------------------------
        message_list = next(v for v in find_list_views(window.contentItem())
                            if v.property("model") is session.messages)

        # Reactions and a forwarded message in the feed; typing in the header and the chat list
        reactions = {"@type": "messageInteractionInfo", "reactions": {
            "@type": "messageReactions", "are_tags": False, "reactions": [
                {"type": {"@type": "reactionTypeEmoji", "emoji": "👍"},
                 "total_count": 3, "is_chosen": True},
                {"type": {"@type": "reactionTypeEmoji", "emoji": "🔥"},
                 "total_count": 1, "is_chosen": False}]}}
        forwarded = {**_msg(9, "Forwarded with a reaction"), "forward_info": {
            "origin": {"@type": "messageOriginHiddenUser", "sender_name": "Jana"}, "date": 1},
            "interaction_info": reactions}
        session.client._dispatch({"@type": "updateNewMessage", "message": forwarded})
        linked = {**_msg(10, "Worth reading https://example.com/post"), "content": {
            "@type": "messageText", "text": {"text": "Worth reading https://example.com/post",
                                             "entities": []},
            "link_preview": {"@type": "linkPreview", "url": "https://example.com/post",
                             "display_url": "example.com/post", "site_name": "Example Blog",
                             "title": "How we rebuilt the message list",
                             "description": {"text": "Variable heights, smooth scrolling and "
                                                     "what we learned along the way."},
                             "author": "", "show_large_media": False,
                             "type": {"@type": "linkPreviewTypeArticle", "photo": None}}}}
        session.client._dispatch({"@type": "updateNewMessage", "message": linked})
        session.client._dispatch({"@type": "updateMessageInteractionInfo", "chat_id": 1,
                                  "message_id": 2, "interaction_info": reactions})
        session.client._dispatch({"@type": "updateChatAction", "chat_id": 1, "topic_id": None,
                                  "sender_id": {"@type": "messageSenderUser", "user_id": 5},
                                  "action": {"@type": "chatActionTyping"}})
        session.client._dispatch({"@type": "updateChatDraftMessage", "chat_id": 2,
                                  "draft_message": {"@type": "draftMessage", "content": {
                                      "@type": "draftMessageContentText",
                                      "text": {"text": "see you at the meetup"}}},
                                  "positions": []})
        await settle(0.2)
        self.assertEqual(session.messages.chatStatus, "typing…")
        message_list.positionViewAtBeginning()
        await settle(0.1)
        self.assertEqual(warnings, [], "QML warnings with reactions and forwards")
        card = next(i for i in _all_items(message_list)
                    if i.objectName() == "linkCard" and i.isVisible())
        self.assertGreater(card.height(), 40)
        _screenshot(window, "reactions-typing-draft")

        # Context menu: actions come from TDLib first, then it pops up with quick reactions
        message_menu = window.findChild(QObject, "messageMenu")
        message_menu.setProperty("messageId", 3)
        message_menu.setProperty("waiting", True)
        session.messages.requestActions(3)
        await wait_until(lambda: (pump(), message_menu.property("opened"))[1])
        await settle()
        self.assertTrue(message_menu.property("actions").get("canEdit"))
        _screenshot(window, "menu-actions")
        # the expand button: every reaction the chat allows, in a scrollable grid
        QMetaObject.invokeMethod(message_menu, "openAllReactions")
        await settle()
        picker = window.findChild(QObject, "reactionPicker")
        self.assertTrue(picker.property("opened"))
        self.assertGreaterEqual(len(picker.property("reactions")), len(DEFAULT_REACTIONS))
        _screenshot(window, "reaction-picker")
        QMetaObject.invokeMethod(picker, "picked", Q_ARG("QVariant", 3),
                                 Q_ARG(str, DEFAULT_REACTIONS[40]))
        await wait_until(lambda: any(
            r["@type"] == "addMessageReaction"
            and r["reaction_type"]["emoji"] == DEFAULT_REACTIONS[40]
            for r in session.client._hub._lib.sent))
        QMetaObject.invokeMethod(picker, "close")
        await settle(0.15)

        # The hover button: the reaction grid opens over the message once TDLib answers
        reaction_picker = window.findChild(QObject, "reactionPicker")
        reaction_picker.setProperty("waitingFor", 2)
        session.messages.requestActions(2)
        await wait_until(lambda: (pump(), reaction_picker.property("opened"))[1])
        await settle()
        self.assertEqual(reaction_picker.property("messageId"), 2)
        _screenshot(window, "hover-reactions")
        QMetaObject.invokeMethod(reaction_picker, "close")
        await settle(0.15)

        # Editing: the composer switches to edit mode and back
        composer = find_item(window.contentItem(), "composerInput")
        session.messages.startEdit(3)
        await wait_until(lambda: (pump(), composer.property("text"))[1].startswith("See"))
        await settle(0.1)
        _screenshot(window, "editing")
        composer_root = composer.parentItem()
        while composer_root is not None and composer_root.property("editingId") is None:
            composer_root = composer_root.parentItem()
        self.assertEqual(composer_root.property("editingId"), 3)
        QMetaObject.invokeMethod(composer_root, "finishEdit")
        pump()
        self.assertEqual(composer_root.property("editingId"), 0)

        # Multi-selection: the bar replaces the composer, Esc clears it
        session.messages.toggleSelected(2)
        session.messages.selectRange(3)
        QMetaObject.invokeMethod(message_list, "positionViewAtIndex",
                                 Q_ARG(int, session.messages.rowOf(2)), Q_ARG(int, 1))
        await settle(0.1)
        selection_bar = find_item(window.contentItem(), "selectionBar")
        self.assertTrue(selection_bar.isVisible())
        self.assertFalse(composer_root.isVisible())
        _screenshot(window, "selection")
        session.messages.clearSelection()
        pump()
        self.assertFalse(selection_bar.isVisible())

        # Delete and forward dialogs
        delete_dialog = window.findChild(QObject, "deleteDialog")
        QMetaObject.invokeMethod(delete_dialog, "ask", Q_ARG("QVariant", 3),
                                 Q_ARG("QVariant", True))
        await settle()
        self.assertTrue(delete_dialog.property("opened"))
        _screenshot(window, "delete")
        QMetaObject.invokeMethod(delete_dialog, "close")
        forward_dialog = window.findChild(QObject, "forwardDialog")
        QMetaObject.invokeMethod(forward_dialog, "pick", Q_ARG("QVariant", 3))
        await settle()
        self.assertEqual(session.chat_picker.rowCount(), 3)
        _screenshot(window, "forward")
        QMetaObject.invokeMethod(forward_dialog, "close")
        await settle(0.15)

        # Files with a caption
        session.composer.stage([PHOTO_PATH, VOICE_PATH])
        send_files = window.findChild(QObject, "sendFilesDialog")
        await settle()
        self.assertTrue(send_files.property("opened"))
        _screenshot(window, "send-files")
        session.composer.unstage(1)
        before = session.messages.rowCount()
        QMetaObject.invokeMethod(send_files, "send")
        await wait_until(lambda: (pump(), session.messages.rowCount())[1] == before + 1)
        await settle(0.15)
        self.assertFalse(send_files.property("opened"))
        photo_request = [r for r in session.client._hub._lib.sent
                         if r["@type"] == "sendMessage"][-1]
        self.assertEqual(photo_request["input_message_content"]["@type"], "inputMessagePhoto")
        self.assertEqual(warnings, [], "QML warnings in M7 dialogs")

        # A chat with unread messages opens on the separator, not at the bottom
        QMetaObject.invokeMethod(main_view, "openChat", Q_ARG("QVariant", LONG_CHAT),
                                 Q_ARG("QVariant", 0))
        await wait_until(lambda: (pump(), session.messages.rowOf(41))[1] >= 0
                         and session.messages._first_unread == 41)
        await settle(0.3)
        separator = next(item for item in _all_items(message_list)
                         if item.objectName() == "unreadSeparator" and item.isVisible())
        top = separator.mapToItem(message_list, 0, 0).y()
        self.assertTrue(0 <= top < 40,
                        f"separator at {top}, list height {message_list.height()}")
        _screenshot(window, "unread")
        self.assertEqual(warnings, [], "QML warnings with the unread separator")
        # opened around the separator: "down" shows the unread count, mentions get "@"
        self.assertFalse(session.messages.atLatest)
        session.client._dispatch({"@type": "updateChatUnreadMentionCount",
                                  "chat_id": LONG_CHAT, "unread_mention_count": 3})
        await settle(0.1)
        latest_jump = find_item(window.contentItem(), "latestJump")
        mention_jump = find_item(window.contentItem(), "mentionJump")
        self.assertTrue(latest_jump.isVisible() and mention_jump.isVisible())
        self.assertEqual(latest_jump.property("count"), 260)
        self.assertEqual(mention_jump.property("count"), 3)
        _screenshot(window, "jump-buttons")
        QMetaObject.invokeMethod(latest_jump, "clicked")
        await wait_until(lambda: (pump(), session.messages.atLatest)[1]
                         and session.messages.rowOf(300) >= 0)
        self.assertEqual(warnings, [], "QML warnings with the jump buttons")

        settings_dialog = window.findChild(QObject, "settingsDialog")
        QMetaObject.invokeMethod(settings_dialog, "open")
        await settle()
        self.assertEqual(warnings, [], "QML warnings in settings")
        _screenshot(window, "settings")
        session.ai.setReads("uk", True)
        self.assertEqual(session.prefs.get("read_languages"), ["uk"])
        self.assertIn("uk", session.ai.readLanguages)
        reads = settings_dialog.findChild(QQuickItem, "readLanguages")
        flick = settings_dialog.property("contentItem")
        flick.setProperty("contentY", max(0.0, reads.mapToItem(
            flick.property("contentItem"), 0, 0).y() - 200))
        await settle()
        _screenshot(window, "settings-languages")
        QMetaObject.invokeMethod(settings_dialog, "close")

        from tgclient.app import dispose_engine

        dispose_engine(engine)  # QML before the objects it binds to
        await session.close()


def _all_items(item: Any) -> list[Any]:
    found = [item]
    for child in item.childItems():
        found.extend(_all_items(child))
    return found


def _screenshot(window: Any, name: str) -> None:
    """TGC_SCREENSHOTS=<dir>: save what the test sees, for checking layouts by eye."""
    target = os.environ.get("TGC_SCREENSHOTS")
    if target:
        theme = os.environ.get("TGC_THEME", "system")
        window.grabWindow().save(os.path.join(target, f"{name}-{theme}.png"))


if __name__ == "__main__":
    unittest.main()
