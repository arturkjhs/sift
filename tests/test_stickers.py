"""Emoji catalog/model, sticker store/model and sending stickers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fakes import FakeLib, new_chat, ok, qt_app, wait_until

from tgclient.store.chats import ChatStore
from tgclient.store.emoji import RECENT, EmojiCatalog
from tgclient.store.files import FileManager
from tgclient.store.stickers import StickerStore
from tgclient.store.users import UserStore
from tgclient.td import TdHub

CHAT = 42


class EmojiCatalogTest(unittest.TestCase):
    def test_groups_search_and_no_skin_tone_variants(self) -> None:
        catalog = EmojiCatalog()
        keys = [c.key for c in catalog.categories]
        self.assertEqual(keys[0], RECENT)
        self.assertIn("smileys", keys)
        self.assertEqual(catalog.items("smileys")[0], ("\U0001F600", "grinning face"))
        everything = [e for c in keys[1:] for e, _ in catalog.items(c)]
        self.assertGreater(len(everything), 1500)
        self.assertFalse(any(ch in "\U0001F3FB\U0001F3FC\U0001F3FD\U0001F3FE\U0001F3FF"
                             for e in everything for ch in e))
        hearts = [e for e, _ in catalog.search("red hea")]
        self.assertIn("❤️", hearts)
        self.assertEqual(catalog.search("   "), [])

    def test_recent_is_ordered_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recent.json"
            catalog = EmojiCatalog(path)
            for emoji in ("\U0001F600", "\U0001F44D", "\U0001F600"):
                catalog.use(emoji)
            self.assertEqual(catalog.recent, ["\U0001F600", "\U0001F44D"])
            self.assertEqual(json.loads(path.read_text()), catalog.recent)
            again = EmojiCatalog(path)
            self.assertEqual([e for e, _ in again.items(RECENT)], ["\U0001F600", "\U0001F44D"])
            self.assertEqual(again.items(RECENT)[0][1], "grinning face")

    def test_model_category_search_and_refresh(self) -> None:
        qt_app()
        from PySide6.QtTest import QAbstractItemModelTester

        from tgclient.models.emoji import EmojiModel, Role

        model = EmojiModel(EmojiCatalog())
        QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
        self.assertEqual(model.property("category"), "smileys")  # no recent yet
        model.setProperty("query", "thumbs up")
        self.assertEqual(model.data(model.index(0), Role.Emoji), "\U0001F44D")
        model.setProperty("query", "")
        model.use("\U0001F44D")
        model.setProperty("category", RECENT)
        self.assertEqual(model.rowCount(), 1)
        model.use("\U0001F600")
        self.assertEqual(model.rowCount(), 1)  # the open grid doesn't jump under the cursor
        model.refresh()
        self.assertEqual(model.data(model.index(0), Role.Name), "grinning face")


def sticker(sid: int, file_id: int, emoji: str = "\U0001F600", fmt: str = "Webp",
            thumb: int = 0) -> dict[str, Any]:
    result: dict[str, Any] = {
        "@type": "sticker", "id": str(sid), "set_id": "77", "width": 512, "height": 480,
        "emoji": emoji, "format": {"@type": f"stickerFormat{fmt}"},
        "sticker": file_(file_id), "thumbnail": None,
    }
    if thumb:
        result["thumbnail"] = {"@type": "thumbnail", "format": {"@type": "thumbnailFormatWebp"},
                               "width": 128, "height": 128, "file": file_(thumb)}
    return result


def file_(fid: int, path: str = "") -> dict[str, Any]:
    return {"@type": "file", "id": fid, "size": 100, "expected_size": 100,
            "local": {"path": path, "is_downloading_completed": bool(path)}, "remote": {}}


class StickerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        qt_app()
        self.requests: list[dict[str, Any]] = []
        self.sets = [{"@type": "stickerSetInfo", "id": "77", "title": "Cats", "name": "cats",
                      "is_archived": False, "covers": [sticker(1, 101)]},
                     {"@type": "stickerSetInfo", "id": "78", "title": "Old", "name": "old",
                      "is_archived": True, "covers": []}]
        self.recent = [sticker(5, 105, "\U0001F44D"), sticker(6, 106, fmt="Tgs", thumb=206)]

        def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
            self.requests.append(req)
            match req["@type"]:
                case "getInstalledStickerSets":
                    return [{"@type": "stickerSets", "total_count": len(self.sets),
                             "sets": self.sets, "@extra": req["@extra"]}]
                case "getRecentStickers":
                    return [{"@type": "stickers", "stickers": self.recent,
                             "@extra": req["@extra"]}]
                case "getStickerSet":
                    return [{"@type": "stickerSet", "id": req["set_id"], "title": "Cats",
                             "stickers": [sticker(1, 101), sticker(2, 102, "\U0001F431")],
                             "@extra": req["@extra"]}]
                case "downloadFile":
                    return [{**file_(req["file_id"], f"/s/{req['file_id']}.webp"),
                             "@extra": req["@extra"]}]
                case "sendMessage":
                    return [{"@type": "message", "id": 900, "chat_id": req["chat_id"],
                             "date": 0, "content": {"@type": "messageSticker"},
                             "@extra": req["@extra"]}]
                case "getChatHistory":
                    return [{"@type": "messages", "total_count": 0, "messages": [],
                             "@extra": req["@extra"]}]
            return [ok(req)]

        self.lib = FakeLib(responder)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.files = FileManager(self.client)
        self.store = StickerStore(self.client, self.files)

        from tgclient.models.stickers import Role, StickerModel

        self.Role = Role
        self.model = StickerModel(self.store, self.files)

    def sent(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["@type"] == kind]

    async def test_loads_on_demand_with_recent_first(self) -> None:
        self.assertEqual(self.sent("getInstalledStickerSets"), [])  # nothing at startup
        self.model.load()
        await wait_until(lambda: self.store.loaded and len(self.model.property("sets")) == 2)
        sets = self.model.property("sets")
        self.assertEqual([s["key"] for s in sets], ["recent", "77"])  # archived set left out
        self.assertEqual(self.sent("getInstalledStickerSets")[0]["sticker_type"],
                         {"@type": "stickerTypeRegular"})
        self.assertFalse(self.sent("getRecentStickers")[0]["is_attached"])
        self.assertEqual(self.model.rowCount(), 2)

        # previews: WebP itself, the thumbnail for an animated (TGS) sticker; lazily
        self.assertEqual(self.model.data(self.model.index(0), self.Role.Source), "")
        self.model.data(self.model.index(1), self.Role.Source)
        await wait_until(lambda: {r["file_id"] for r in self.sent("downloadFile")} >= {105, 206})
        await wait_until(lambda: self.model.data(self.model.index(1), self.Role.Source)
                         == "image://tg/0/sticker/206")

    async def test_switching_sets_and_updates(self) -> None:
        self.model.load()
        await wait_until(lambda: self.store.loaded and self.model.rowCount() == 2)
        self.model.setProperty("currentSet", "77")
        await wait_until(lambda: self.model.rowCount() == 2 and not self.model.property("loading"))
        self.assertEqual(self.model.sticker(1)["emoji"], "\U0001F431")
        self.assertEqual(self.sent("getStickerSet")[0]["set_id"], "77")

        self.sets = self.sets[:1] + [{"@type": "stickerSetInfo", "id": "79", "title": "Dogs",
                                      "is_archived": False, "covers": []}]
        self.lib.push({"@type": "updateInstalledStickerSets",
                       "sticker_type": {"@type": "stickerTypeRegular"},
                       "sticker_set_ids": ["77", "79"]})
        await wait_until(lambda: [s["key"] for s in self.model.property("sets")]
                         == ["recent", "77", "79"])

    async def test_no_recent_opens_the_first_set(self) -> None:
        self.recent = []
        self.model.load()
        await wait_until(lambda: self.model.property("currentSet") == "77")
        await wait_until(lambda: self.model.rowCount() == 2)

    async def test_send_sticker_request_shape(self) -> None:
        from tgclient.models.messages import MessageListModel

        chats = ChatStore(self.client, self.files)
        self.lib.push(new_chat(CHAT, "Chat", 1))
        await wait_until(lambda: CHAT in chats.chats)
        messages = MessageListModel(self.client, chats, UserStore(self.client))
        messages.open(CHAT)
        messages.sendSticker(sticker(2, 102, "\U0001F431"), 55)
        await wait_until(lambda: bool(self.sent("sendMessage")))
        request = self.sent("sendMessage")[0]
        self.assertEqual(request["reply_to"],
                         {"@type": "inputMessageReplyToMessage", "message_id": 55})
        self.assertEqual(request["input_message_content"], {
            "@type": "inputMessageSticker",
            "sticker": {"@type": "inputSticker",
                        "sticker": {"@type": "inputFileId", "id": 102},
                        "thumbnail": None, "width": 512, "height": 480},
            "emoji": "\U0001F431",
        })


if __name__ == "__main__":
    unittest.main()
