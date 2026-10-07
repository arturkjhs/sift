"""Link previews: parsing TDLib's linkPreview, the feed's role, the composer's preview and
sending without one."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

import test_composer
from fakes import wait_until
from test_composer import ComposerCase

from tgclient.store.link_preview import message_link_preview, parse


def photo(file_id: int, width: int = 800, height: int = 400) -> dict[str, Any]:
    return {"@type": "photo", "minithumbnail": None, "sizes": [
        {"@type": "photoSize", "type": "x", "width": width, "height": height,
         "photo": {"@type": "file", "id": file_id, "size": 10, "expected_size": 10,
                   "local": {"path": "", "is_downloading_completed": False}, "remote": {}}}]}


ARTICLE = {"@type": "linkPreview", "url": "https://example.com/post", "display_url":
           "example.com/post", "site_name": "Example", "title": "A post",
           "description": {"@type": "formattedText", "text": "Some\n  words", "entities": []},
           "author": "", "type": {"@type": "linkPreviewTypeArticle", "photo": photo(70)},
           "has_large_media": True, "show_large_media": True,
           "show_media_above_description": False, "skip_confirmation": False,
           "show_above_text": False, "instant_view_version": 0}


class ParseTest(unittest.TestCase):
    def test_article_video_user_and_empty(self) -> None:
        preview = parse(ARTICLE)
        assert preview is not None
        self.assertEqual((preview.site, preview.title, preview.description, preview.large),
                         ("Example", "A post", "Some words", True))
        self.assertEqual(preview.image["id"], 70)

        video = {**ARTICLE, "show_large_media": False, "type": {
            "@type": "linkPreviewTypeVideo", "start_timestamp": 0, "cover": None, "video": {
                "duration": 200, "width": 640, "height": 360, "thumbnail": {
                    "format": {"@type": "thumbnailFormatJpeg"}, "width": 320, "height": 180,
                    "file": {"id": 71}}}}}
        preview = parse(video)
        self.assertEqual((preview.image["id"], preview.label, preview.large),
                         (71, "Video · 3:20", False))

        user = {**ARTICLE, "type": {"@type": "linkPreviewTypeUser", "is_bot": False,
                                    "photo": {"small": {"id": 72}, "big": {"id": 73}}}}
        self.assertEqual(parse(user).image["id"], 72)
        self.assertFalse(parse(user).large)

        bare = {**ARTICLE, "title": "", "description": {"text": ""}, "type": {
            "@type": "linkPreviewTypeUnsupported"}}
        self.assertIsNone(parse(bare))
        self.assertIsNone(message_link_preview({"@type": "messagePhoto"}))
        self.assertEqual(message_link_preview({"@type": "messageText", "text": {"text": "x"},
                                               "link_preview": ARTICLE}).title, "A post")


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    if req["@type"] == "downloadFile":  # stays remote: the placeholder is enough here
        return [{"@type": "file", "id": req["file_id"], "size": 10, "expected_size": 10,
                 "local": {"path": "", "is_downloading_active": True}, "remote": {},
                 "@extra": req["@extra"]}]
    if req["@type"] == "getLinkPreview":
        if "example.com" not in req["text"]["text"]:
            return [{"@type": "error", "code": 404, "message": "Not Found",
                     "@extra": req["@extra"]}]
        return [{**ARTICLE, "@extra": req["@extra"]}]
    return test_composer.responder(req)


class PreviewCase(ComposerCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.lib._responder = responder


class ComposerPreviewTest(PreviewCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        patcher = mock.patch("tgclient.models.composer.PREVIEW_DELAY", 0.01)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_preview_while_typing_and_sending_without_it(self) -> None:
        self.messages.open(2)
        self.composer.setDraft("look https://example.com/post", 0)
        await wait_until(lambda: self.composer.linkPreview.get("title") == "A post")
        self.assertEqual(self.composer.linkPreview["site"], "Example")
        self.composer.setDraft("look https://example.com/post !", 0)  # same link: no new ask
        self.assertEqual(len(self.sent("getLinkPreview")), 1)

        self.composer.removeLinkPreview()
        self.assertEqual(self.composer.linkPreview, {})
        options = self.composer.sendOptions()
        self.assertTrue(options["noPreview"])
        self.messages.sendMessage("look https://example.com/post", 0, options)
        await wait_until(lambda: bool(self.sent("sendMessage")))
        sent = self.sent("sendMessage")[0]["input_message_content"]
        self.assertTrue(sent["link_preview_options"]["is_disabled"])
        self.composer.sent()
        self.assertFalse(self.composer.linkPreviewOff)

        self.composer.setDraft("no links here", 0)
        self.assertEqual(self.composer.linkPreview, {})
        self.messages.sendMessage("plain", 0, self.composer.sendOptions())
        await wait_until(lambda: len(self.sent("sendMessage")) == 2)
        self.assertIsNone(self.sent("sendMessage")[1]["input_message_content"][
            "link_preview_options"])


class FeedRoleTest(PreviewCase):
    async def test_role(self) -> None:
        from tgclient.models.messages import Role

        self.messages.open(1)
        await self.push({"@type": "updateNewMessage", "message": {
            "@type": "message", "id": 5, "chat_id": 1, "date": 1, "is_outgoing": False,
            "sender_id": {"@type": "messageSenderUser", "user_id": 1},
            "content": {"@type": "messageText", "text": {"text": "https://example.com/post",
                                                         "entities": []},
                        "link_preview": ARTICLE}}})
        data = self.messages.data(self.messages.index(0), Role.LinkPreview)
        self.assertEqual((data["title"], data["large"], data["image"]), ("A post", True, ""))
        self.assertEqual((data["width"], data["height"]), (320, 160))
        await wait_until(lambda: any(r["@type"] == "downloadFile" and r["file_id"] == 70
                                     for r in self.lib.sent))


if __name__ == "__main__":
    unittest.main()
