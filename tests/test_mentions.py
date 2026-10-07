"""@mentions in the composer: member suggestions, mentions of people without a username sent
as textEntityTypeMentionName."""

from __future__ import annotations

import unittest
from typing import Any

import test_composer
from fakes import new_chat, wait_until
from test_composer import ComposerCase

from tgclient.store.richtext import mention_names

GROUP = 9


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    extra = req.get("@extra")
    match req["@type"]:
        case "searchChatMembers":
            members = [(5, "ol"), (6, "pe"), (1, "me")]
            found = [{"member_id": {"@type": "messageSenderUser", "user_id": u}}
                     for u, key in members if key.startswith(req["query"][:2].lower())
                     or not req["query"]]
            return [{"@type": "chatMembers", "total_count": len(found), "members": found,
                     "@extra": extra}]
        case "parseMarkdown":  # what TDLib does with [text](url): a text URL entity
            text = req["text"]["text"]
            if "](tg://user?id=6)" in text:
                start = text.index("[")
                name = text[start + 1:text.index("]")]
                plain = text[:start] + name + text[text.index(")") + 1:]
                return [{"@type": "formattedText", "text": plain, "@extra": extra, "entities": [
                    {"offset": start, "length": len(name), "type": {
                        "@type": "textEntityTypeTextUrl", "url": "tg://user?id=6"}}]}]
    return test_composer.responder(req)


class MentionsTest(ComposerCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.lib._responder = responder
        await self.push(
            new_chat(GROUP, "Hiking", 30, "chatTypeSupergroup"),
            {"@type": "updateUser", "user": {"id": 5, "first_name": "Olena", "last_name": "K",
                                             "usernames": {"active_usernames": ["olena"]}}},
            {"@type": "updateUser", "user": {"id": 6, "first_name": "Petr", "last_name": "_N"}},
            {"@type": "updateOption", "name": "my_id",
             "value": {"@type": "optionValueInteger", "value": "1"}})

    async def test_suggestions_and_sending(self) -> None:
        self.messages.open(GROUP)
        self.composer.findMentions("")
        await wait_until(lambda: len(self.composer.mentions) == 2)  # not me
        self.composer.findMentions("pe")
        await wait_until(lambda: [m["userId"] for m in self.composer.mentions] == [6])
        petr = self.composer.mentions[0]
        self.assertEqual(petr["insert"], "[Petr \\_N](tg://user?id=6) ")
        self.composer.findMentions("ol")
        await wait_until(lambda: self.composer.mentions[0]["insert"] == "@olena ")
        self.composer.stopMentions()
        self.assertEqual(self.composer.mentions, [])

        self.messages.sendMessage("hi [Petr](tg://user?id=6)!", 0, {})
        await wait_until(lambda: bool(self.sent("sendMessage")))
        text = self.sent("sendMessage")[0]["input_message_content"]["text"]
        self.assertEqual(text["text"], "hi Petr!")
        self.assertEqual(text["entities"][0]["type"],
                         {"@type": "textEntityTypeMentionName", "user_id": 6})

        self.messages.open(1)  # a private chat: no suggestions
        self.composer.findMentions("")
        self.assertEqual(self.composer.mentions, [])

    def test_mention_names(self) -> None:
        url = {"offset": 0, "length": 1, "type": {"@type": "textEntityTypeTextUrl",
                                                  "url": "https://example.com"}}
        self.assertEqual(mention_names({"text": "x", "entities": [url]})["entities"], [url])


if __name__ == "__main__":
    unittest.main()
