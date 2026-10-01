"""Loads the real QML offscreen with a fake TDLib: catches QML errors and wiring mistakes."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import Any

from fakes import FakeLib, auth_state, error, new_chat, ok, qt_app, wait_until

from tgclient.config import Settings


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    match req["@type"]:
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
    async def test_login_then_chat_list(self) -> None:
        app = qt_app()
        from PySide6.QtQuick import QQuickItem

        from tgclient.app import Session, create_engine
        from tgclient.ui.shell import ShellController

        settings = Settings(api_id=1, api_hash="x", data_dir=Path("/tmp/tgc-test"),
                            use_test_dc=False, td_log_level=0, log_level="WARNING")
        session = Session(settings, lib=FakeLib(responder))
        shell = ShellController(asyncio.Event())

        warnings: list[str] = []
        engine = create_engine(session, shell, on_warnings=warnings.extend)
        self.assertTrue(engine.rootObjects(), f"QML failed to load: {warnings}")
        window = engine.rootObjects()[0]

        def pump() -> None:
            for _ in range(5):
                app.processEvents()

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

        await session.close()
        del engine


if __name__ == "__main__":
    unittest.main()
