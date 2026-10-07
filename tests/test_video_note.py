"""Video messages: frames cropped to a square, encoded with the sound into an MP4, sent as a
video note (no camera offscreen: frames are fed directly)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np
from fakes import FakeLib, ok, qt_app, wait_until

from tgclient.td import TdHub


class Sink:
    def __init__(self) -> None:
        self.sent: list[tuple[Any, ...]] = []

    def send_video_note_to(self, *args: Any) -> None:
        self.sent.append(args)


class VideoNoteTest(unittest.IsolatedAsyncioTestCase):
    async def test_record_encode_send(self) -> None:
        qt_app()
        import av
        from PySide6.QtGui import QColor, QImage

        from tgclient.ui.video_note import SIDE, VideoNoteRecorder, square

        frame = QImage(640, 480, QImage.Format.Format_RGB32)
        frame.fill(QColor("#3FB295"))
        squared = square(frame)
        self.assertEqual((squared.width(), squared.height()), (SIDE, SIDE))

        lib = FakeLib(lambda req: [ok(req)])
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        sink = Sink()
        folder = Path(tempfile.mkdtemp())
        recorder = VideoNoteRecorder(hub.create_client(), sink, folder)
        recorder._chat_id = 42
        recorder._set_state("recording")  # what _begin() does once the camera runs
        import time

        recorder._started = time.monotonic() - 2
        for i in range(40):
            frame.fill(QColor(20 + i * 5, 120, 100))
            recorder.add_frame(frame)
        self.assertEqual(recorder.frame, 40)
        tone = (np.sin(np.arange(48_000 * 2) / 48_000 * 2 * np.pi * 440) * 8000).astype(np.int16)
        recorder._pcm = bytearray(tone.tobytes())
        recorder.finish()
        await wait_until(lambda: bool(sink.sent), timeout=20)
        chat_id, path, duration, length, reply_to = sink.sent[0]
        self.assertEqual((chat_id, length, reply_to), (42, SIDE, 0))
        self.assertGreaterEqual(duration, 1)
        with av.open(path) as media:
            kinds = {s.type for s in media.streams}
            video = media.streams.video[0]
            self.assertEqual((video.width, video.height), (SIDE, SIDE))
        self.assertEqual(kinds, {"video", "audio"})
        self.assertFalse(recorder.busy)


if __name__ == "__main__":
    unittest.main()
