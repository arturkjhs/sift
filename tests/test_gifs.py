"""The GIF tab: saved GIFs, search through the @gif bot, sending one by its file."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from fakes import FakeLib, new_chat, ok, qt_app, wait_until
from test_history import CHAT

from tgclient.store.chats import ChatStore
from tgclient.store.users import UserStore
from tgclient.td import TdHub


def animation(file_id: int, width: int = 320, height: int = 240) -> dict[str, Any]:
    return {"@type": "animation", "duration": 2, "width": width, "height": height,
            "file_name": "a.mp4", "mime_type": "video/mp4",
            "thumbnail": {"format": {"@type": "thumbnailFormatJpeg"}, "width": 90,
                          "height": 67, "file": {"id": file_id + 1, "size": 10,
                                                 "expected_size": 10, "remote": {},
                                                 "local": {"path": "",
                                                           "is_downloading_completed": False}}},
            "animation": {"id": file_id, "size": 1000, "expected_size": 1000, "remote": {},
                          "local": {"path": "", "is_downloading_completed": False}}}


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    extra = req.get("@extra")
    match req["@type"]:
        case "getSavedAnimations":
            return [{"@type": "animations", "animations": [animation(10)], "@extra": extra}]
        case "searchPublicChat":
            return [{"@type": "chat", "id": 140, "type": {"@type": "chatTypePrivate",
                                                          "user_id": 140}, "@extra": extra}]
        case "getInlineQueryResults":
            assert (req["bot_user_id"], req["query"]) == (140, "cats")
            return [{"@type": "inlineQueryResults", "inline_query_id": "5", "next_offset": "",
                     "@extra": extra, "results": [
                         {"@type": "inlineQueryResultAnimation", "id": "a", "title": "",
                          "animation": animation(20, 200, 100)},
                         {"@type": "inlineQueryResultPhoto", "id": "b"}]}]
        case "downloadFile":
            return [{"@type": "file", "id": req["file_id"], "size": 10, "expected_size": 10,
                     "local": {"path": "", "is_downloading_active": True}, "remote": {},
                     "@extra": extra}]
        case "getChatHistory":
            return [{"@type": "messages", "total_count": 0, "messages": [], "@extra": extra}]
    return [ok(req)]


class GifTest(unittest.IsolatedAsyncioTestCase):
    async def test_saved_search_send(self) -> None:
        qt_app()
        from tgclient.models.gifs import GifModel, Role
        from tgclient.models.messages import MessageListModel

        lib = FakeLib(responder)
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        client = hub.create_client()
        chats = ChatStore(client)
        lib.push(new_chat(CHAT, "Friends", 1))
        await wait_until(lambda: CHAT in chats.chats)
        gifs = GifModel(client, chats.files)
        gifs.load()
        await wait_until(lambda: gifs.rowCount() == 1)
        with mock.patch("tgclient.models.gifs.SEARCH_DELAY", 0):
            gifs.setProperty("query", "cats")
            await wait_until(lambda: gifs.rowCount() == 1
                             and gifs.animation(0)["animation"]["id"] == 20)
        self.assertEqual(gifs.data(gifs.index(0), Role.Ratio), 2.0)
        gifs.data(gifs.index(0), Role.Source)
        await wait_until(lambda: any(r["@type"] == "downloadFile" and r["file_id"] == 21
                                     for r in lib.sent))
        gifs.setProperty("query", "")
        self.assertEqual(gifs.animation(0)["animation"]["id"], 10)  # saved ones again

        messages = MessageListModel(client, chats, UserStore(client))
        messages.open(CHAT)
        messages.sendAnimation(gifs.animation(0), 0)
        await wait_until(lambda: any(r["@type"] == "sendMessage" for r in lib.sent))
        sent = next(r for r in lib.sent if r["@type"] == "sendMessage")
        content = sent["input_message_content"]
        self.assertEqual((content["@type"], content["animation"]["animation"]),
                         ("inputMessageAnimation", {"@type": "inputFileId", "id": 10}))


if __name__ == "__main__":
    unittest.main()
