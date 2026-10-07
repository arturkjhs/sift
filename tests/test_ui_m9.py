"""M9 views in the real QML, offscreen: album grid, animated stickers, GIF, custom emoji,
the media viewer, the account switcher, QR login, the recording bar, the update banner."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

import test_m9
from fakes import FakeLib, auth_state, new_chat, ok, qt_app, wait_until
from test_m9 import PHOTO, TGS, WEBM, file_obj, photo_msg, text_msg
from test_ui import _all_items, _screenshot

from tgclient.config import Settings

GIF = test_m9.TMP / "gif.mp4"


def setUpModule() -> None:
    test_m9.setUpModule()
    import av
    import numpy as np

    with av.open(str(GIF), "w") as out:
        stream = out.add_stream("mpeg4", rate=15)
        stream.width, stream.height, stream.pix_fmt = 160, 120, "yuv420p"
        for i in range(15):
            frame = np.full((120, 160, 3), (20 + i * 10, 120, 100), np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(frame, format="rgb24")):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)


def sticker(mid: int, fid: int, kind: str) -> dict[str, Any]:
    return {**text_msg(mid), "content": {"@type": "messageSticker", "sticker": {
        "@type": "sticker", "id": str(fid), "set_id": "1", "width": 512, "height": 512,
        "emoji": "\U0001F600", "format": {"@type": kind},
        "full_type": {"@type": "stickerFullTypeRegular"}, "thumbnail": None,
        "sticker": file_obj(fid)}}}


HISTORY = [
    {**text_msg(9, "Nice trip \U0001F680"), "content": {"@type": "messageText", "text": {
        "text": "Nice trip \U0001F680", "entities": [{"offset": 10, "length": 2, "type": {
            "@type": "textEntityTypeCustomEmoji", "custom_emoji_id": "77"}}]}},
     "interaction_info": {"reactions": {"@type": "messageReactions", "are_tags": False,
                                        "reactions": [{"type": {
                                            "@type": "reactionTypeCustomEmoji",
                                            "custom_emoji_id": "77"}, "total_count": 2,
                                            "is_chosen": False}]}}},
    {**text_msg(8), "content": {"@type": "messageAnimation", "caption": {"text": ""},
                                "animation": {"duration": 1, "width": 160, "height": 120,
                                              "file_name": "gif.mp4", "mime_type": "video/mp4",
                                              "animation": file_obj(800, size=3000)}}},
    sticker(7, 700, "stickerFormatWebm"),
    sticker(6, 600, "stickerFormatTgs"),
    photo_msg(5, album=9, file_id=50), photo_msg(4, album=9, file_id=40),
    photo_msg(3, album=9, caption="Prague, day one", file_id=30),
    text_msg(1, "Photos from the weekend:", sender=6),
]
PATHS = {50: PHOTO, 40: PHOTO, 30: PHOTO, 700: WEBM, 600: TGS, 800: GIF, 900: TGS}


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    extra = req.get("@extra")
    match req["@type"]:
        case "getOption":
            return [auth_state("authorizationStateReady"),
                    new_chat(test_m9.CHAT, "Weekend", 10, "chatTypeSupergroup"),
                    {"@type": "updateUser", "user": {"id": 5, "first_name": "Olena"}},
                    {"@type": "updateUser", "user": {"id": 6, "first_name": "Petr"}},
                    {"@type": "updateUser", "user": {"id": 1, "first_name": "Svitlana",
                                                     "last_name": "K"}},
                    {"@type": "updateOption", "name": "my_id",
                     "value": {"@type": "optionValueInteger", "value": "1"}},
                    {"@type": "optionValueString", "value": "x", "@extra": extra}]
        case "loadChats":
            return [{"@type": "error", "code": 404, "message": "Not Found", "@extra": extra}]
        case "getChatHistory":
            page = [] if req["from_message_id"] else HISTORY
            return [{"@type": "messages", "total_count": len(page), "messages": page,
                     "@extra": extra}]
        case "downloadFile":
            path = PATHS.get(req["file_id"])
            return [{**file_obj(req["file_id"], str(path) if path else ""), "@extra": extra}]
        case "getCustomEmojiStickers":
            return [{"@type": "stickers", "@extra": extra, "stickers": [{
                "@type": "sticker", "id": "1", "set_id": "2", "width": 100, "height": 100,
                "emoji": "\U0001F680", "format": {"@type": "stickerFormatTgs"},
                "full_type": {"@type": "stickerFullTypeCustomEmoji", "custom_emoji_id": "77",
                              "needs_repainting": False},
                "thumbnail": None, "sticker": file_obj(900)}]}]
        case "close":
            return [ok(req), auth_state("authorizationStateClosed")]
    return [ok(req)]


def qr_responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    match req["@type"]:
        case "getOption":
            return [auth_state("authorizationStateWaitTdlibParameters"),
                    {"@type": "optionValueString", "value": "x", "@extra": req["@extra"]}]
        case "setTdlibParameters":
            return [auth_state("authorizationStateWaitPhoneNumber"), ok(req)]
        case "requestQrCodeAuthentication":
            return [auth_state("authorizationStateWaitOtherDeviceConfirmation",
                               link="tg://login?token=AQIDBAUGBwgJCgsMDQ4PEBESExQ"), ok(req)]
        case "close":
            return [ok(req), auth_state("authorizationStateClosed")]
    return [ok(req)]


class M9ViewsTest(unittest.IsolatedAsyncioTestCase):
    async def test_media_accounts_qr(self) -> None:
        app = qt_app()
        from PySide6.QtCore import QMetaObject, QObject
        from PySide6.QtQuick import QQuickItem

        from tgclient.app import Session, create_engine, dispose_engine
        from tgclient.services.ai_store import AiStore
        from tgclient.services.search_index import SearchIndex
        from tgclient.ui.shell import ShellController

        data = Path(tempfile.mkdtemp())
        settings = Settings(api_id=1, api_hash="x", data_dir=data, use_test_dc=False,
                            td_log_level=0, log_level="WARNING")
        session = Session(settings, lib=FakeLib(responder), ai_store=AiStore(":memory:"),
                          search_index=SearchIndex(":memory:"))
        warnings: list[str] = []
        engine = create_engine(session, ShellController(asyncio.Event()),
                               on_warnings=warnings.extend)
        window = engine.rootObjects()[0]

        def pump() -> None:
            for _ in range(5):
                app.processEvents()

        async def settle(seconds: float = 0.3) -> None:
            for _ in range(int(seconds / 0.02)):
                pump()
                await asyncio.sleep(0.02)

        await session.start()
        await wait_until(lambda: (pump(), session.auth.step)[1] == "ready")
        main_view = window.findChild(QQuickItem, "mainView")
        main_view.setProperty("selectedChatId", test_m9.CHAT)
        session.messages.open(test_m9.CHAT)
        await wait_until(lambda: (pump(), session.messages.rowCount())[1] == len(HISTORY))
        await settle(1.2)  # downloads, animation frames, custom emoji
        html = session.messages.data(session.messages.index(0),
                                     session.messages.roleNames().keys().__iter__().__next__())
        self.assertIsNotNone(html)
        animated = [i for i in _all_items(window.contentItem())
                    if i.metaObject().className().startswith("AnimatedImage")]
        self.assertTrue(any(i.property("ready") for i in animated), "no sticker animated")
        self.assertEqual(warnings, [], "QML warnings in the M9 feed")
        _screenshot(window, "m9-feed")
        from PySide6.QtCore import Q_ARG

        feed = next(i for i in _all_items(window.contentItem())
                    if i.metaObject().className().startswith("QQuickListView")
                    and i.property("model") is session.messages)
        QMetaObject.invokeMethod(feed, "positionViewAtIndex", Q_ARG(int, 4), Q_ARG(int, 1))
        await settle(0.4)
        grid = [i for i in _all_items(window.contentItem())
                if i.metaObject().className().startswith("QQuickImage")
                and "/media/" in i.property("source").toString()]
        self.assertGreaterEqual(len(grid), 3, "album grid not drawn")
        _screenshot(window, "m9-album")

        # the viewer over an album photo
        session.messages.activateMedia(4)
        await wait_until(lambda: (pump(), session.viewer.ready)[1])
        await settle()
        viewer = window.findChild(QObject, "mediaViewer")
        self.assertTrue(viewer.property("opened"))
        _screenshot(window, "m9-viewer")
        session.viewer.next()
        await settle(0.2)
        self.assertEqual(session.viewer.messageId, 5)
        QMetaObject.invokeMethod(viewer, "close")
        await settle(0.2)
        self.assertFalse(session.viewer.active)

        # recording bar (no microphone offscreen: the state is set directly)
        import time

        session.recorder._started = time.monotonic() - 7
        session.recorder._state = "recording"
        session.recorder._level = 0.6
        session.recorder.changed.emit()
        session.recorder.levelChanged.emit()
        await settle(0.2)
        _screenshot(window, "m9-recording")
        session.recorder._state = "idle"
        session.recorder.changed.emit()

        # recording a video message (no camera offscreen: a frame is fed directly)
        from PySide6.QtGui import QColor, QImage

        session.video_recorder._started = time.monotonic() - 12
        session.video_recorder._state = "recording"
        camera_frame = QImage(640, 480, QImage.Format.Format_RGB32)
        camera_frame.fill(QColor("#6C7F99"))
        session.video_recorder.add_frame(camera_frame)
        session.video_recorder.changed.emit()
        await settle(0.2)
        preview = next(i for i in _all_items(window.contentItem())
                       if i.objectName() == "videoPreview")
        self.assertTrue(preview.isVisible())
        _screenshot(window, "m9-video-note")
        session.video_recorder._state = "idle"
        session.video_recorder._frames = []
        session.video_recorder.changed.emit()

        # the update banner
        updates = engine._tgclient_refs[5]
        from tgclient.services.updates import Release

        updates._release = Release("0.9.0", "https://example.com", "")
        updates._set("available")
        await settle(0.1)
        banner = next(i for i in _all_items(window.contentItem())
                      if i.objectName() == "updateBanner")
        self.assertTrue(banner.isVisible())

        # account menu
        switcher = next(i for i in _all_items(window.contentItem())
                        if i.objectName() == "accountSwitcher")
        menu = switcher.findChild(QObject, "accountMenu")
        QMetaObject.invokeMethod(menu, "open")
        await settle()
        _screenshot(window, "m9-accounts")
        QMetaObject.invokeMethod(menu, "close")
        await settle(0.15)
        self.assertEqual(warnings, [], "QML warnings in M9 views")
        dispose_engine(engine)
        await session.close()

        # QR login screen
        qr_session = Session(dataclass_replace(settings, data / "qr"), lib=FakeLib(qr_responder),
                             ai_store=AiStore(":memory:"), search_index=SearchIndex(":memory:"))
        qr_engine = create_engine(qr_session, ShellController(asyncio.Event()),
                                  on_warnings=warnings.extend)
        qr_window = qr_engine.rootObjects()[0]
        start = asyncio.ensure_future(qr_session.start())
        await wait_until(lambda: (pump(), qr_session.auth.step)[1] == "phone")
        qr_session.auth.requestQr()
        await wait_until(lambda: (pump(), qr_session.auth.step)[1] == "qr")
        await settle()
        image = next(i for i in _all_items(qr_window.contentItem())
                     if i.objectName() == "qrImage")
        await wait_until(lambda: (pump(), image.property("implicitWidth"))[1] > 0)  # loaded
        _screenshot(qr_window, "m9-qr")
        self.assertEqual(warnings, [], "QML warnings on the QR screen")
        start.cancel()
        dispose_engine(qr_engine)
        await qr_session.close()


def dataclass_replace(settings: Settings, data_dir: Path) -> Settings:
    import dataclasses

    return dataclasses.replace(settings, data_dir=data_dir)


if __name__ == "__main__":
    unittest.main()
