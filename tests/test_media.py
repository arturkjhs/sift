"""Media extraction, waveform decoding, file state and media roles of the message model."""

from __future__ import annotations

import base64
import time
import unittest
from typing import Any

from fakes import FakeLib, new_chat, ok, qt_app, wait_until

from tgclient.store.chats import ChatStore
from tgclient.store.files import USER_PRIORITY, FileManager
from tgclient.store.media import decode_waveform, duration_text, extract, fit, human_size
from tgclient.store.users import UserStore
from tgclient.td import TdHub

CHAT = 42


def file(fid: int, size: int = 1000, path: str = "", done: bool = False,
         downloading: bool = False, downloaded: int = 0) -> dict[str, Any]:
    return {"@type": "file", "id": fid, "size": size, "expected_size": size,
            "local": {"path": path, "is_downloading_completed": done,
                      "is_downloading_active": downloading, "downloaded_size": downloaded},
            "remote": {"is_uploading_active": False, "uploaded_size": 0}}


def photo_content(mini: bytes = b"\xff\xd8mini") -> dict[str, Any]:
    return {"@type": "messagePhoto", "caption": {"text": "", "entities": []}, "photo": {
        "minithumbnail": {"width": 40, "height": 30, "data": base64.b64encode(mini).decode()},
        "sizes": [
            {"type": "m", "width": 320, "height": 240, "photo": file(11)},
            {"type": "x", "width": 800, "height": 600, "photo": file(12)},
            {"type": "w", "width": 2560, "height": 1920, "photo": file(13)},
        ]}}


def pack_waveform(samples: list[int]) -> bytes:
    bits = 0
    for i, value in enumerate(samples):
        bits |= (value & 31) << (i * 5)
    return bits.to_bytes((len(samples) * 5 + 7) // 8, "little")


class ExtractTest(unittest.TestCase):
    def test_photo_picks_display_and_full_size(self) -> None:
        media = extract(photo_content())
        assert media is not None
        self.assertEqual(media.kind, "photo")
        self.assertEqual(media.preview["id"], 12)   # biggest that fits 1280
        self.assertEqual(media.file["id"], 13)      # biggest overall, for "open"
        self.assertEqual((media.width, media.height), (800, 600))
        self.assertEqual(media.minithumbnail, b"\xff\xd8mini")

    def test_sticker_static_vs_animated(self) -> None:
        static = extract({"@type": "messageSticker", "sticker": {
            "width": 512, "height": 512, "emoji": "😀", "format": {"@type": "stickerFormatWebp"},
            "sticker": file(20), "thumbnail": None}})
        animated = extract({"@type": "messageSticker", "sticker": {
            "width": 512, "height": 512, "emoji": "🔥", "format": {"@type": "stickerFormatTgs"},
            "sticker": file(21), "thumbnail": {"format": {"@type": "thumbnailFormatWebp"},
                                               "width": 128, "height": 128, "file": file(22)}}})
        self.assertEqual(static.preview["id"], 20)
        self.assertEqual(animated.preview["id"], 22)
        self.assertEqual(animated.emoji, "🔥")

    def test_voice_and_document(self) -> None:
        voice = extract({"@type": "messageVoiceNote", "voice_note": {
            "duration": 7, "waveform": base64.b64encode(pack_waveform([1, 2, 3])).decode(),
            "mime_type": "audio/ogg", "voice": file(30)}})
        self.assertEqual((voice.kind, voice.duration, voice.file["id"]), ("voice", 7, 30))
        doc = extract({"@type": "messageDocument", "document": {
            "file_name": "contract.pdf", "mime_type": "application/pdf", "document": file(31)}})
        self.assertEqual((doc.kind, doc.file_name, doc.preview), ("document", "contract.pdf", None))
        self.assertIsNone(extract({"@type": "messageText", "text": {"text": "x"}}))

    def test_waveform_decoding(self) -> None:
        samples = [0, 31, 15, 7, 31, 1, 2, 30]
        decoded = decode_waveform(pack_waveform(samples), bars=8)
        self.assertEqual(len(decoded), 8)
        self.assertAlmostEqual(decoded[1], 1.0)
        self.assertAlmostEqual(decoded[2], 15 / 31)
        self.assertEqual(decoded[0], 0.12)  # floor so silence is still visible
        self.assertEqual(decode_waveform(b"", bars=5), [0.15] * 5)

    def test_helpers(self) -> None:
        self.assertEqual(fit(800, 600, 320, 320), (320, 240))
        self.assertEqual(fit(600, 1800, 320, 320, min_side=120), (120, 320))
        self.assertEqual(human_size(1536), "1.5 KB")
        self.assertEqual(human_size(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(duration_text(75), "1:15")
        self.assertEqual(duration_text(3725), "1:02:05")


class FileTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.downloads: list[dict[str, Any]] = []

        def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
            if req["@type"] == "downloadFile":
                self.downloads.append(req)
                return [{**file(req["file_id"], downloading=True), "@extra": req["@extra"]}]
            if req["@type"] == "getChatHistory":
                return [{"@type": "messages", "total_count": 0, "messages": self.history
                         if req["from_message_id"] == 0 else [], "@extra": req["@extra"]}]
            return [ok(req)]

        self.history: list[dict[str, Any]] = []
        self.lib = FakeLib(responder)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.files = FileManager(self.client)

    async def push(self, *events: dict[str, Any]) -> None:
        seen: list[Any] = []
        off = self.client.on("updateTestMarker", seen.append)
        for event in (*events, {"@type": "updateTestMarker"}):
            self.lib.push(event)
        await wait_until(lambda: bool(seen))
        off()


class FileManagerTest(FileTestCase):
    async def test_register_never_overwrites_fresher_state(self) -> None:
        await self.push({"@type": "updateFile", "file": file(1, path="/f", done=True)})
        self.files.register(file(1))  # stale embedded snapshot
        self.assertEqual(self.files.path(1), "/f")

    async def test_download_dedup_and_notifications(self) -> None:
        changed: list[int] = []
        self.files.subscribe(changed.append)
        self.files.download(5)
        self.files.download(5)
        await wait_until(lambda: len(self.downloads) == 1 and 5 in changed)
        self.assertEqual(self.files.get(5).status, "downloading")
        await self.push({"@type": "updateFile", "file": file(5, path="/x", done=True,
                                                             downloaded=1000)})
        self.assertEqual(self.files.get(5).status, "ready")
        self.assertEqual(self.files.get(5).progress, 1.0)


class MediaRolesTest(FileTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        qt_app()
        from PySide6.QtTest import QAbstractItemModelTester

        from tgclient.models.messages import MessageListModel, Role

        self.Role = Role
        now = int(time.time())

        def msg(mid: int, content: dict[str, Any]) -> dict[str, Any]:
            return {"@type": "message", "id": mid, "chat_id": CHAT, "date": now,
                    "sender_id": {"@type": "messageSenderUser", "user_id": 5},
                    "is_outgoing": False, "content": content}

        self.history = [
            msg(3, {"@type": "messageDocument", "document": {
                "file_name": "report.pdf", "mime_type": "application/pdf",
                "document": file(40, size=2048)}}),
            msg(2, {"@type": "messageVoiceNote", "voice_note": {
                "duration": 12, "waveform": base64.b64encode(pack_waveform([5] * 50)).decode(),
                "mime_type": "audio/ogg", "voice": file(41)}}),
            msg(1, photo_content()),
        ]
        self.chats = ChatStore(self.client, self.files)
        await self.push(new_chat(CHAT, "Chat", 1))
        self.model = MessageListModel(self.client, self.chats, UserStore(self.client))
        self.tester = QAbstractItemModelTester(
            self.model, QAbstractItemModelTester.FailureReportingMode.Fatal)
        self.model.open(CHAT)
        await wait_until(lambda: self.model.rowCount() == 3 and not self.model.loading)

    def role(self, row: int, role: Any) -> Any:
        return self.model.data(self.model.index(row), role)

    async def test_photo_placeholder_then_real_image(self) -> None:
        self.assertEqual(self.role(2, self.Role.MediaKind), "photo")
        self.assertEqual(self.role(2, self.Role.MediaSource), "image://tg/mini/12")
        self.assertEqual((self.role(2, self.Role.MediaWidth), self.role(2, self.Role.MediaHeight)),
                         (320, 240))
        await wait_until(lambda: any(d["file_id"] == 12 for d in self.downloads))

        changed: list[int] = []
        self.model.dataChanged.connect(lambda top, _bottom, _roles: changed.append(top.row()))
        await self.push({"@type": "updateFile", "file": file(12, path="/p.jpg", done=True)})
        self.assertIn(2, changed)
        self.assertEqual(self.role(2, self.Role.MediaSource), "image://tg/media/12")

    async def test_document_click_downloads_then_cancels(self) -> None:
        self.assertEqual(self.role(0, self.Role.FileName), "report.pdf")
        self.assertEqual(self.role(0, self.Role.FileInfo), "2.0 KB")
        self.model.activateMedia(3)
        await wait_until(lambda: any(d["file_id"] == 40 for d in self.downloads))
        request = next(d for d in self.downloads if d["file_id"] == 40)
        self.assertEqual(request["priority"], USER_PRIORITY)
        await wait_until(lambda: self.role(0, self.Role.FileState) == "downloading")
        self.model.activateMedia(3)
        await wait_until(lambda: any(r["@type"] == "cancelDownloadFile" for r in self.lib.sent))

    async def test_voice_roles(self) -> None:
        self.assertEqual(self.role(1, self.Role.MediaKind), "voice")
        self.assertEqual(self.role(1, self.Role.Duration), "0:12")
        self.assertEqual(len(self.role(1, self.Role.Waveform)), 40)
        self.assertEqual(self.role(1, self.Role.MediaLabel), "")


class ImageProviderTest(unittest.TestCase):
    def test_rounded_corners_are_transparent(self) -> None:
        qt_app()
        from PySide6.QtGui import QColor, QImage

        from tgclient.ui.images import rounded

        source = QImage(100, 50, QImage.Format.Format_RGB32)
        source.fill(QColor("#ff0000"))
        result = rounded(source, 60, 60, 12)
        self.assertEqual((result.width(), result.height()), (60, 60))
        self.assertEqual(result.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(result.pixelColor(30, 30).red(), 255)


if __name__ == "__main__":
    unittest.main()
