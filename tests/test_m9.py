"""M9: albums, animated stickers and GIFs, custom emoji, voice recording, the media viewer,
QR login, several accounts, updates."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, ClassVar

import httpx
import numpy as np
from fakes import FakeLib, auth_state, new_chat, ok, qt_app, wait_until

from tgclient.accounts import FIRST, AccountRegistry
from tgclient.store.album import album_layout
from tgclient.store.chats import ChatStore
from tgclient.store.media import decode_waveform, encode_waveform
from tgclient.store.richtext import Palette, formatted_to_html
from tgclient.store.users import UserStore
from tgclient.td import TdHub

TMP = Path(tempfile.mkdtemp(prefix="tgc-m9-"))
TGS = TMP / "sticker.tgs"
WEBM = TMP / "sticker.webm"
PHOTO = TMP / "photo.png"
CHAT = 42
NOW = int(time.time())


def setUpModule() -> None:
    lottie = {"v": "5.5.2", "fr": 60, "ip": 0, "op": 60, "w": 512, "h": 512, "layers": [{
        "ty": 4, "ind": 1, "ip": 0, "op": 60, "st": 0,
        "ks": {"o": {"a": 0, "k": 100}, "r": {"a": 1, "k": [{"t": 0, "s": [0]},
                                                             {"t": 60, "s": [360]}]},
               "p": {"a": 0, "k": [256, 256]}, "a": {"a": 0, "k": [0, 0]},
               "s": {"a": 0, "k": [100, 100]}},
        "shapes": [{"ty": "rc", "p": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [200, 200]},
                    "r": {"a": 0, "k": 20}},
                   {"ty": "fl", "c": {"a": 0, "k": [0.05, 0.48, 0.4, 1]}, "o": {"a": 0, "k": 100}}],
    }]}
    TGS.write_bytes(gzip.compress(json.dumps(lottie).encode()))
    import av

    with av.open(str(WEBM), "w") as out:
        stream = out.add_stream("libvpx-vp9", rate=30)
        stream.width = stream.height = 128
        stream.pix_fmt = "yuva420p"
        for i in range(12):
            frame = np.zeros((128, 128, 4), np.uint8)
            frame[32:96, 16 + i:80 + i] = (200, 50, 50, 255)
            for packet in stream.encode(av.VideoFrame.from_ndarray(frame, format="rgba")):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    qt_app()
    from PySide6.QtGui import QColor, QImage

    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor("#6C7F99"))
    image.save(str(PHOTO))


def file_obj(fid: int, path: str = "", size: int = 1000) -> dict[str, Any]:
    return {"@type": "file", "id": fid, "size": size, "expected_size": size,
            "local": {"path": path, "is_downloading_completed": bool(path)}, "remote": {}}


def photo_msg(mid: int, album: int = 0, caption: str = "", file_id: int = 0,
              path: str = "") -> dict[str, Any]:
    fid = file_id or mid * 10
    return {"@type": "message", "id": mid, "chat_id": CHAT, "is_outgoing": False,
            "date": NOW - 100 + mid, "media_album_id": str(album),
            "sender_id": {"@type": "messageSenderUser", "user_id": 5},
            "content": {"@type": "messagePhoto", "caption": {"text": caption, "entities": []},
                        "photo": {"sizes": [{"type": "x", "width": 800, "height": 600,
                                             "photo": file_obj(fid, path)}]}}}


def text_msg(mid: int, text: str = "hi", sender: int = 5) -> dict[str, Any]:
    return {"@type": "message", "id": mid, "chat_id": CHAT, "is_outgoing": False,
            "date": NOW - 200 + mid, "media_album_id": "0",
            "sender_id": {"@type": "messageSenderUser", "user_id": sender},
            "content": {"@type": "messageText", "text": {"text": text, "entities": []}}}


class HelpersTest(unittest.TestCase):
    def test_album_layout_fills_rows(self) -> None:
        for count in range(1, 11):
            cells = album_layout(([(800, 600), (600, 800), (1000, 500)] * 4)[:count], 320)
            self.assertEqual(len(cells), count)
            rows: dict[int, list[Any]] = {}
            for cell in cells:
                rows.setdefault(cell.y, []).append(cell)
            for row in rows.values():
                self.assertEqual(row[-1].x + row[-1].width, 320)  # each row spans the width
        stacked = album_layout([(2000, 800), (2000, 800)], 320)
        self.assertEqual(stacked[1].x, 0)  # two wide pictures go on top of each other

    def test_waveform_roundtrip(self) -> None:
        levels = [i % 32 for i in range(100)]
        data = encode_waveform(levels)
        self.assertEqual(len(data), 63)  # 100 samples * 5 bits
        decoded = decode_waveform(data, bars=100)
        self.assertEqual([round(v * 31) for v in decoded][5:10], [5, 6, 7, 8, 9])

    def test_custom_emoji_in_rich_text(self) -> None:
        text = {"text": "go \U0001F680!", "entities": [{"offset": 3, "length": 2, "type": {
            "@type": "textEntityTypeCustomEmoji", "custom_emoji_id": "77"}}]}
        known = formatted_to_html(text, Palette(), custom_emoji=lambda i: "image://e/" + i)
        self.assertIn('<img src="image://e/77"', known)
        self.assertNotIn("\U0001F680", known)
        self.assertIn("\U0001F680", formatted_to_html(text, Palette(),
                                                      custom_emoji=lambda i: None))


class AnimationTest(unittest.IsolatedAsyncioTestCase):
    async def test_frames_of_tgs_and_alpha_webm(self) -> None:
        from tgclient.ui.animation import cache, first_frame, load_frames, sniff

        self.assertEqual((sniff(str(TGS)), sniff(str(WEBM)), sniff(str(PHOTO))),
                         ("tgs", "webm", "image"))
        tgs = load_frames(str(TGS), 128, 128)
        self.assertEqual(len(tgs.frames), 31)  # 60 fps played at 30
        self.assertAlmostEqual(tgs.fps, 30.0)
        webm = load_frames(str(WEBM), 64, 64)
        self.assertEqual(len(webm.frames), 12)
        self.assertEqual(webm.frames[0].pixelColor(0, 0).alpha(), 0)  # alpha kept
        self.assertEqual(webm.frames[0].pixelColor(24, 32).alpha(), 255)
        self.assertFalse(first_frame(str(TGS), 64, 64).isNull())
        got: list[Any] = []
        cache.get(str(TGS), 64, 64, got.append)
        await wait_until(lambda: bool(got))
        cache.get(str(TGS), 64, 64, got.append)  # cached: answered right away
        self.assertEqual(len(got), 2)
        self.assertIs(got[0], got[1])


class AnimatedItemTest(unittest.IsolatedAsyncioTestCase):
    async def test_item_destroyed_while_frames_render(self) -> None:
        """Scrolling a sticker away (or switching chats) before its frames are ready used to
        raise "Signal source has been deleted" from the worker's callback."""
        qt_app()
        from PySide6.QtCore import QCoreApplication, QEvent

        from tgclient.ui.animation import AnimatedImage, FrameCache

        errors: list[Any] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: errors.append(context.get("exception")))
        from tgclient.ui import animation

        animation.cache = FrameCache()  # not served from frames earlier tests rendered
        gone = AnimatedImage()
        gone.setWidth(160)
        gone.setHeight(160)
        gone.source = str(TGS)
        alive = AnimatedImage()
        alive.setWidth(160)
        alive.setHeight(160)
        alive.source = str(TGS)  # waits for the same frames
        gone.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        await wait_until(lambda: alive.ready)
        self.assertEqual(errors, [])


class VoiceTest(unittest.TestCase):
    def test_encode_opus_and_waveform(self) -> None:
        import av

        from tgclient.ui.recorder import RATE, encode_ogg_opus, waveform

        t = np.arange(int(RATE * 1.5)) / RATE
        pcm = (np.sin(2 * np.pi * 300 * t) * 9000 * (t / t.max())).astype(np.int16).tobytes()
        path = TMP / "voice.ogg"
        duration = encode_ogg_opus(pcm, str(path))
        self.assertAlmostEqual(duration, 1.5, places=2)
        with av.open(str(path)) as container:
            self.assertEqual(container.streams.audio[0].codec_context.name, "opus")
        wave = decode_waveform(waveform(pcm), bars=100)
        self.assertLess(wave[2], wave[-1])  # louder towards the end


class ModelCase(unittest.IsolatedAsyncioTestCase):
    history: ClassVar[list[dict[str, Any]]] = []

    def responder(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        extra = req.get("@extra")
        match req["@type"]:
            case "getChatHistory":
                page = [] if req["from_message_id"] else self.history
                return [{"@type": "messages", "total_count": len(page), "messages": page,
                         "@extra": extra}]
            case "getCustomEmojiStickers":
                return [{"@type": "stickers", "@extra": extra, "stickers": [{
                    "@type": "sticker", "id": "1", "set_id": "2", "width": 100, "height": 100,
                    "emoji": "\U0001F680", "format": {"@type": "stickerFormatTgs"},
                    "full_type": {"@type": "stickerFullTypeCustomEmoji",
                                  "custom_emoji_id": "77", "needs_repainting": False},
                    "thumbnail": None, "sticker": file_obj(900, str(TGS))}]}]
            case "downloadFile":
                path = {300: str(PHOTO), 20: str(PHOTO), 30: str(PHOTO),
                        40: str(PHOTO)}.get(req["file_id"], "")
                return [{**file_obj(req["file_id"], path), "@extra": extra}]
        return [ok(req)]

    async def asyncSetUp(self) -> None:
        qt_app()
        from PySide6.QtTest import QAbstractItemModelTester

        from tgclient.models.messages import MessageListModel, Role
        from tgclient.store.custom_emoji import CustomEmojiStore

        self.Role = Role
        self.lib = FakeLib(self.responder)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.emoji = CustomEmojiStore(self.client, self.chats.files)
        await self.push(new_chat(CHAT, "Trip", 1, "chatTypeSupergroup"),
                        {"@type": "updateUser", "user": {"id": 5, "first_name": "Olena"}})
        self.model = MessageListModel(self.client, self.chats, self.users, emoji=self.emoji)
        self.tester = QAbstractItemModelTester(
            self.model, QAbstractItemModelTester.FailureReportingMode.Fatal)

    async def push(self, *events: dict[str, Any]) -> None:
        seen: list[Any] = []
        off = self.client.on("updateTestMarker", seen.append)
        for event in (*events, {"@type": "updateTestMarker"}):
            self.lib.push(event)
        await wait_until(lambda: bool(seen))
        off()

    def role(self, row: int, role: Any) -> Any:
        return self.model.data(self.model.index(row), role)

    async def open(self, rows: int) -> None:
        self.model.open(CHAT)
        await wait_until(lambda: not self.model.loading and self.model.rowCount() == rows)


class AlbumModelTest(ModelCase):
    history: ClassVar[list[dict[str, Any]]] = [photo_msg(4, album=9), photo_msg(3, album=9),
               photo_msg(2, album=9, caption="Prague"), text_msg(1, sender=6)]

    async def test_album_rows(self) -> None:
        await self.open(4)
        self.assertEqual([self.role(r, self.Role.AlbumHidden) for r in range(4)],
                         [False, True, True, False])
        self.assertEqual(self.role(0, self.Role.MediaKind), "album")
        items = self.role(0, self.Role.AlbumItems)
        self.assertEqual([i["messageId"] for i in items], [2, 3, 4])  # oldest first
        self.assertEqual(items[0]["w"], 320)  # 3 items: one wide on top, two below
        self.assertIn("Prague", self.role(0, self.Role.Html))  # the caption of another member
        # the name shows on the album: the older neighbour is past its hidden members
        self.assertTrue(self.role(0, self.Role.ShowSender))
        await self.push({"@type": "updateNewMessage", "message": photo_msg(5, album=9)})
        self.assertEqual(len(self.role(0, self.Role.AlbumItems)), 4)
        self.assertTrue(self.role(1, self.Role.AlbumHidden))

    async def test_viewer_walks_media(self) -> None:
        from tgclient.models.viewer import ViewerModel

        await self.open(4)
        viewer = ViewerModel(self.model, self.chats.files)
        requested: list[Any] = []
        self.model.viewerRequested.connect(requested.append)
        self.model.activateMedia(3)
        self.assertEqual(requested, [3])
        viewer.open(3)
        self.assertEqual((viewer.index, viewer.count, viewer.kind), (1, 3, "photo"))
        await wait_until(lambda: viewer.ready)
        self.assertTrue(viewer.source.startswith("file://"))
        viewer.prev()
        self.assertEqual((viewer.messageId, viewer.caption), (2, "Prague"))
        target = TMP / "saved.png"
        await wait_until(lambda: viewer.ready)
        from PySide6.QtCore import QUrl

        self.assertTrue(viewer.saveAs(QUrl.fromLocalFile(str(target))))
        self.assertEqual(target.read_bytes(), PHOTO.read_bytes())
        viewer.close()
        self.assertFalse(viewer.active)


class StickerAndEmojiModelTest(ModelCase):
    history: ClassVar[list[dict[str, Any]]] = [
        {**text_msg(3, "go \U0001F680"), "content": {"@type": "messageText", "text": {
            "text": "go \U0001F680", "entities": [{"offset": 3, "length": 2, "type": {
                "@type": "textEntityTypeCustomEmoji", "custom_emoji_id": "77"}}]}}},
        {**text_msg(2), "content": {"@type": "messageSticker", "sticker": {
            "@type": "sticker", "id": "5", "set_id": "6", "width": 512, "height": 512,
            "emoji": "\U0001F600", "format": {"@type": "stickerFormatTgs"},
            "full_type": {"@type": "stickerFullTypeRegular"}, "thumbnail": None,
            "sticker": file_obj(300)}}},
        text_msg(1),
    ]

    async def test_animated_sticker_and_custom_emoji(self) -> None:
        await self.open(3)
        self.assertEqual(self.role(1, self.Role.StickerFormat), "tgs")
        self.assertEqual(self.role(1, self.Role.PlaybackPath), "")  # starts the download
        await wait_until(lambda: self.role(1, self.Role.PlaybackPath) == str(PHOTO))
        self.assertNotIn("<img", self.role(0, self.Role.Html))  # emoji not known yet
        await wait_until(lambda: "<img" in self.role(0, self.Role.Html))
        self.assertIn('src="image://tg/0/sticker/900"', self.role(0, self.Role.Html))

    async def test_send_voice(self) -> None:
        await self.open(3)
        self.model.send_voice_to(CHAT, "/tmp/v.ogg", 3, encode_waveform([31] * 100), 2)
        await wait_until(lambda: any(r["@type"] == "sendMessage" for r in self.lib.sent))
        sent = next(r for r in self.lib.sent if r["@type"] == "sendMessage")
        voice = sent["input_message_content"]["voice_note"]
        self.assertEqual(sent["input_message_content"]["@type"], "inputMessageVoiceNote")
        self.assertEqual((voice["@type"], voice["duration"], voice["voice_note"]["path"]),
                         ("inputVoiceNote", 3, "/tmp/v.ogg"))
        self.assertEqual(sent["reply_to"]["message_id"], 2)


class QrLoginTest(unittest.IsolatedAsyncioTestCase):
    async def test_qr_flow_and_image(self) -> None:
        qt_app()
        from urllib.parse import quote

        from tgclient.td.auth import AuthFlow, TdlibParams
        from tgclient.ui.auth_controller import AuthController
        from tgclient.ui.qr import matrix, render

        link = "tg://login?token=abc123"

        def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
            if req["@type"] == "requestQrCodeAuthentication":
                return [auth_state("authorizationStateWaitOtherDeviceConfirmation", link=link),
                        ok(req)]
            return [ok(req)]

        lib = FakeLib(responder)
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        client = hub.create_client()
        auth = AuthController()
        flow = AuthFlow(client, auth, TdlibParams(1, "x", TMP / "db", TMP / "files"))
        task = asyncio.ensure_future(flow.run())
        lib.push(auth_state("authorizationStateWaitPhoneNumber"))
        await wait_until(lambda: auth.step == "phone")
        auth.requestQr()
        await wait_until(lambda: auth.step == "qr")
        self.assertEqual(auth.qrSource, "image://qr/" + quote(link, safe=""))
        lib.push(auth_state("authorizationStateReady"))
        await asyncio.wait_for(task, 2)
        rows = matrix(link)
        self.assertEqual(len(rows), len(rows[0]))
        image = render(link, 200)
        colors = {image.pixelColor(x, y).name() for x in range(0, 200, 7) for y in range(0, 200, 7)}
        self.assertEqual(colors, {"#ffffff", "#111111"})


class AccountRegistryTest(unittest.TestCase):
    def test_add_remove_persist(self) -> None:
        data = Path(tempfile.mkdtemp())
        registry = AccountRegistry(data)
        self.assertEqual(registry.keys(), [FIRST])
        added = registry.add()
        (data / added.folder / "prod").mkdir(parents=True)
        registry.set_active(added.key)
        again = AccountRegistry(data)
        self.assertEqual((again.keys(), again.active), ([FIRST, added.key], added.key))
        again.remove(added.key)
        self.assertFalse((data / added.folder).exists())
        self.assertEqual((AccountRegistry(data).keys(), AccountRegistry(data).active),
                         ([FIRST], FIRST))


class AccountManagerTest(unittest.IsolatedAsyncioTestCase):
    """Two logged-in accounts on one hub: switching, notifications, badge, logging out."""

    def responder(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        match req["@type"]:
            case "getOption":
                return [auth_state("authorizationStateReady"),
                        {"@type": "optionValueString", "value": "x", "@extra": req["@extra"]}]
            case "loadChats":
                return [{"@type": "error", "code": 404, "message": "Not Found",
                         "@extra": req["@extra"]}]
            case "logOut" | "close":
                return [ok(req), auth_state("authorizationStateClosed")]
        return [ok(req)]

    async def test_accounts(self) -> None:
        qt_app()
        import dataclasses

        from tgclient.app import Session
        from tgclient.config import Settings
        from tgclient.prefs import Prefs
        from tgclient.services.ai_store import AiStore
        from tgclient.services.search_index import SearchIndex
        from tgclient.ui.accounts import AccountManager
        from tgclient.ui.notifications import NullBackend

        data = Path(tempfile.mkdtemp())
        settings = Settings(api_id=1, api_hash="x", data_dir=data, use_test_dc=False,
                            td_log_level=0, log_level="WARNING")
        lib = FakeLib(self.responder)
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        backend = NullBackend()
        prefs = Prefs(data / "prefs.json")
        registry = AccountRegistry(data)
        registry.add()

        def make(account: Any) -> Session:
            return Session(dataclasses.replace(settings, account=account.folder), hub=hub,
                           account_key=account.key, prefs=prefs, ai_store=AiStore(":memory:"),
                           search_index=SearchIndex(":memory:"), notification_backend=backend,
                           shared_notifications=True)

        manager = AccountManager(registry, make)
        backend.on_activated = manager.handle_notification_click
        bound: list[str] = []
        manager.set_binder(lambda s: bound.append(s.account_key))
        badges: list[int] = []
        manager.on_badge = badges.append
        manager.start()
        first, second = registry.keys()
        await wait_until(lambda: all(s.auth.step == "ready" for s in manager.sessions.values()))
        self.assertEqual(manager.sessions[first].files.account, first)
        self.assertNotEqual(manager.sessions[second].settings.database_dir,
                            manager.sessions[first].settings.database_dir)
        self.assertEqual(len(manager.accounts), 2)

        manager.switchTo(second)
        self.assertEqual((manager.activeKey, bound), (second, [second]))

        # a notification of the first account: keys carry the account, a click switches back
        client_of_first = manager.sessions[first].client.client_id
        chat_event = new_chat(7, "Olena", 5)
        lib.push(chat_event, client_of_first)
        requested: list[Any] = []
        manager.chatRequested.connect(requested.append)
        lib.push({"@type": "updateNotificationGroup", "notification_group_id": 1, "chat_id": 7,
                  "added_notifications": [{"@type": "notification", "id": 1, "is_silent": False,
                                           "type": {"@type": "notificationTypeNewMessage",
                                                    "message": text_msg(1) | {"chat_id": 7},
                                                    "show_preview": True}}],
                  "removed_notification_ids": []}, client_of_first)
        await wait_until(lambda: bool(backend.shown))
        self.assertTrue(backend.shown[0].key.startswith(f"{first}/"))
        backend.on_activated(backend.shown[0].key)
        self.assertEqual((manager.activeKey, requested), (first, [7]))

        for key, count in ((first, 3), (second, 4)):
            lib.push({"@type": "updateUnreadMessageCount", "chat_list": {
                "@type": "chatListMain"}, "unread_count": count, "unread_unmuted_count": count},
                manager.sessions[key].client.client_id)
        await wait_until(lambda: badges and badges[-1] == 7)

        manager.switchTo(second)
        manager.logOut()
        await wait_until(lambda: second not in manager.sessions)
        self.assertEqual((registry.keys(), manager.activeKey), ([first], first))
        self.assertTrue(any(c == manager.sessions[first].client.client_id or r["@type"] == "logOut"
                            for c, r in lib.sent_by))
        await manager.close()


class UpdatesTest(unittest.IsolatedAsyncioTestCase):
    def test_versions_and_assets(self) -> None:
        from tgclient.services.updates import asset_suffix, install_kind, is_newer

        self.assertTrue(is_newer("v0.3.0", "0.2.9"))
        self.assertFalse(is_newer("v0.3.0", "0.3.0"))
        self.assertFalse(is_newer("v9.0.0", "0.1.0.dev+abc"))  # local builds don't nag
        self.assertTrue(is_newer("v0.10.0", "0.9.1-3-gabc"))
        self.assertEqual(install_kind({"APPIMAGE": "/x"}, "linux"), "appimage")
        self.assertEqual(install_kind({"FLATPAK_ID": "x"}, "linux"), "page")
        self.assertEqual(install_kind({}, "darwin"), "dmg")
        self.assertEqual(asset_suffix("dmg", "arm64"), "-macos-arm64.dmg")
        self.assertEqual(asset_suffix("appimage", "AMD64"), "-linux-x86_64.AppImage")

    async def test_check_download_and_replace(self) -> None:
        from tgclient.services.updates import UpdateChecker, replace_appimage

        payload = b"new appimage"
        release = {"tag_name": "v0.5.0", "html_url": "https://github.com/o/r/releases/v0.5.0",
                   "body": "notes", "draft": False, "prerelease": False, "assets": [
                       {"name": "tgclient-0.5.0-linux-x86_64.AppImage", "size": len(payload),
                        "browser_download_url": "https://example.com/a.AppImage"},
                       {"name": "tgclient-0.5.0-macos-arm64.dmg", "size": 1,
                        "browser_download_url": "https://example.com/a.dmg"}]}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.github.com":
                self.assertEqual(request.url.path, "/repos/o/r/releases/latest")
                return httpx.Response(200, json=release)
            return httpx.Response(200, content=payload)

        transport = httpx.MockTransport(handler)
        checker = UpdateChecker("o/r", "0.4.0", "appimage", transport=transport,
                                machine="x86_64")
        found = await checker.latest()
        assert found is not None
        self.assertEqual((found.version, found.asset_name),
                         ("0.5.0", "tgclient-0.5.0-linux-x86_64.AppImage"))
        progress: list[float] = []
        path = await checker.download(found, TMP / "dl", progress.append)
        self.assertEqual((path.read_bytes(), progress[-1]), (payload, 1.0))
        current = TMP / "tgclient.AppImage"
        current.write_bytes(b"old")
        replace_appimage(path, current)
        self.assertEqual(current.read_bytes(), payload)
        self.assertTrue(os.access(current, os.X_OK))
        self.assertIsNone(await UpdateChecker("o/r", "0.5.0", "appimage",
                                              transport=transport).latest())


if __name__ == "__main__":
    unittest.main()
