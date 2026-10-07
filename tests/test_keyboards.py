"""Bot keyboards: inline buttons (URL, callback with the bot's answer, copy) and the reply
keyboard shown instead of typing."""

from __future__ import annotations

import base64
import unittest
from typing import Any
from unittest import mock

from fakes import wait_until
from test_history import msg
from test_message_actions import ActionCase

from tgclient.store.keyboards import inline_rows, reply_keyboard

DATA = base64.b64encode(b"like").decode()
INLINE = {"@type": "replyMarkupInlineKeyboard", "rows": [
    [{"text": "👍 Like", "type": {"@type": "inlineKeyboardButtonTypeCallback", "data": DATA}},
     {"text": "Site", "type": {"@type": "inlineKeyboardButtonTypeUrl",
                               "url": "https://example.com"}}],
    [{"text": "Copy code", "type": {"@type": "inlineKeyboardButtonTypeCopyText",
                                    "text": "XYZ-42"}},
     {"text": "Play", "type": {"@type": "inlineKeyboardButtonTypeCallbackGame"}}]]}
KEYBOARD = {"@type": "replyMarkupShowKeyboard", "one_time": True, "is_persistent": False,
            "input_field_placeholder": "Pick one", "rows": [
                [{"text": "Yes", "type": {"@type": "keyboardButtonTypeText"}},
                 {"text": "No", "type": {"@type": "keyboardButtonTypeText"}}],
                [{"text": "Share phone",
                  "type": {"@type": "keyboardButtonTypeRequestPhoneNumber"}}]]}


class ParseTest(unittest.TestCase):
    def test_parse(self) -> None:
        rows = inline_rows({"reply_markup": INLINE})
        self.assertEqual([[b["kind"] for b in row] for row in rows],
                         [["callback", "url"], ["copy", "game"]])
        self.assertFalse(rows[1][1]["supported"])
        keyboard = reply_keyboard({"id": 9, "reply_markup": KEYBOARD})
        self.assertEqual(keyboard["rows"], [["Yes", "No"]])  # phone requests left out
        self.assertEqual((keyboard["oneTime"], keyboard["placeholder"]), (True, "Pick one"))
        self.assertEqual(inline_rows({}), [])
        self.assertEqual(reply_keyboard(None), {})


class KeyboardModelTest(ActionCase):
    async def test_inline_buttons(self) -> None:
        def answer(req: dict[str, Any]) -> list[dict[str, Any]] | None:
            if req["@type"] == "getCallbackQueryAnswer":
                assert req["payload"] == {"@type": "callbackQueryPayloadData", "data": DATA}
                return [{"@type": "callbackQueryAnswer", "text": "Thanks!", "show_alert": True,
                         "url": "", "@extra": req["@extra"]}]
            return None

        self.server.hook = answer
        await self.open_loaded()
        await self.push({"@type": "updateNewMessage", "message": {**msg(7, "Rate us"),
                                                                  "reply_markup": INLINE}})
        row = self.model.rowOf(7)
        self.assertEqual(len(self.role(row, self.Role.InlineKeyboard)), 2)
        answers: list[tuple[str, bool]] = []
        self.model.botAnswer.connect(lambda text, alert: answers.append((text, alert)))
        self.model.pressButton(7, 0, 0)
        await wait_until(lambda: bool(answers))
        self.assertEqual(answers, [("Thanks!", True)])
        with mock.patch("tgclient.models.messages.QDesktopServices") as desktop:
            self.model.pressButton(7, 0, 1)
            self.assertEqual(desktop.openUrl.call_args.args[0].toString(), "https://example.com")
        from PySide6.QtGui import QGuiApplication

        self.model.pressButton(7, 1, 0)
        self.assertEqual(QGuiApplication.clipboard().text(), "XYZ-42")
        self.model.pressButton(7, 1, 1)
        self.assertIn("official apps", answers[-1][0])

    async def test_reply_keyboard(self) -> None:
        def answer(req: dict[str, Any]) -> list[dict[str, Any]] | None:
            if req["@type"] == "getMessage" and req["message_id"] == 3:
                return [{**msg(3, "Choose"), "reply_markup": KEYBOARD, "@extra": req["@extra"]}]
            return None

        self.server.hook = answer
        await self.push({"@type": "updateNewChat", "chat": {
            "id": 77, "title": "Bot", "type": {"@type": "chatTypePrivate", "user_id": 77},
            "positions": [], "reply_markup_message_id": 3}})
        self.model.open(77)
        self.assertEqual(self.model.replyKeyboard, {})  # asks for the message first
        await wait_until(lambda: self.model.replyKeyboard.get("rows") == [["Yes", "No"]])
        self.model.sendKeyboardButton("Yes")
        self.assertEqual(self.model.replyKeyboard, {})  # one-time: hidden after use
        await wait_until(lambda: bool(self.sent("sendMessage")))
        self.assertEqual(self.sent("sendMessage")[0]["input_message_content"]["text"]["text"],
                         "Yes")
        await self.push({"@type": "updateChatReplyMarkup", "chat_id": 77,
                         "reply_markup_message": None})
        self.assertEqual(self.model.replyKeyboard, {})


if __name__ == "__main__":
    unittest.main()
