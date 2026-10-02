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
    new_chat,
    ok,
    qt_app,
    wait_until,
)

from tgclient.config import Settings

VOICE_PATH = os.path.join(tempfile.gettempdir(), "tgc-test-voice.ogg")
STICKER_COLORS = {301: "#E8A33D", 302: "#3FB295", 303: "#D4695B", 304: "#6C7F99"}


def _sticker_path(file_id: int) -> str:
    return os.path.join(tempfile.gettempdir(), f"tgc-test-sticker-{file_id}.png")


def _sticker(file_id: int, emoji: str) -> dict[str, Any]:
    return {"@type": "sticker", "id": str(file_id), "set_id": "77", "width": 512,
            "height": 512, "emoji": emoji, "format": {"@type": "stickerFormatWebp"},
            "sticker": _file(file_id), "thumbnail": None}


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    match req["@type"]:
        case "getMessage" if req["message_id"] == 5:
            return [{**_media(5, {"@type": "messageVoiceNote", "voice_note": {
                "duration": 3, "waveform": "", "mime_type": "audio/ogg", "voice": _file(51)}}),
                "@extra": req["@extra"]}]
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
                    ok(req)]
        case "loadChats":
            return [error(req, 404, "Not Found")]
        case "getChatHistory":
            if req["from_message_id"] != 0:
                return [{"@type": "messages", "total_count": 0, "messages": [],
                         "@extra": req["@extra"]}]
            return [{"@type": "messages", "total_count": 5, "@extra": req["@extra"], "messages": [
                _media(5, {"@type": "messageVoiceNote", "voice_note": {
                    "duration": 3, "waveform": "", "mime_type": "audio/ogg", "voice": _file(51)}}),
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

        router = FakeRouter("- Meet at the office [m3]\n- Bubble width discussion [m2, m1]")
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

        await wait_until(lambda: (pump(), session.chat_list.rowCount())[1] == 2)
        pump()

        def find_list_views(item: QQuickItem) -> list[QQuickItem]:
            found = [item] if item.metaObject().className().startswith("QQuickListView") else []
            for child in item.childItems():
                found.extend(find_list_views(child))
            return found

        views = find_list_views(window.contentItem())
        counts = sorted(v.property("count") for v in views)
        self.assertIn(2, counts, f"chat list not rendered, ListView counts: {counts}")
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

        settings_dialog = window.findChild(QObject, "settingsDialog")
        QMetaObject.invokeMethod(settings_dialog, "open")
        await settle()
        self.assertEqual(warnings, [], "QML warnings in settings")
        _screenshot(window, "settings")
        QMetaObject.invokeMethod(settings_dialog, "close")

        await session.close()
        del engine


def _screenshot(window: Any, name: str) -> None:
    """TGC_SCREENSHOTS=<dir>: save what the test sees, for checking layouts by eye."""
    target = os.environ.get("TGC_SCREENSHOTS")
    if target:
        theme = os.environ.get("TGC_THEME", "system")
        window.grabWindow().save(os.path.join(target, f"{name}-{theme}.png"))


if __name__ == "__main__":
    unittest.main()
