"""Recording video messages (the round ones): the camera through QtMultimedia, frames cropped
to a square and kept as JPEG while recording, the microphone as PCM; on Send both are encoded
to an H.264/AAC MP4 with PyAV in a worker thread and sent as a video note.

QML: start() → `recording`, `seconds`; the preview is `image://camera/<n>` (round, the latest
frame; `frame` counts up). finish() sends, cancel() drops. While recording, the chat sees
"recording a video message".
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PySide6.QtCore import (
    Property,
    QBuffer,
    QByteArray,
    QIODevice,
    QObject,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QImage, QPainter, QPainterPath
from PySide6.QtQuick import QQuickImageProvider

from ..td.client import TdClient, TdError
from .recorder import RATE

log = logging.getLogger(__name__)

SIDE = 384  # pixels; Telegram's video notes are square, up to 640
FPS = 25
MAX_SECONDS = 60
MIN_SECONDS = 1.0
ACTION_INTERVAL = 5.0
KEEP_DAYS = 7


class VideoNoteSink(Protocol):
    def send_video_note_to(self, chat_id: int, path: str, duration: int, length: int,
                           reply_to: int = 0) -> None: ...


def square(image: QImage, side: int = SIDE) -> QImage:
    """Centre square of a camera frame scaled to `side` (as recorded: only the preview is
    mirrored, like a selfie view)."""
    edge = min(image.width(), image.height())
    x, y = (image.width() - edge) // 2, (image.height() - edge) // 2
    cropped = image.copy(x, y, edge, edge)
    return cropped.scaled(side, side, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation).convertToFormat(
        QImage.Format.Format_RGB888)


def jpeg(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG", 88)
    return bytes(data.data())


def _video_codec() -> str:
    import av

    for name in ("h264_videotoolbox", "libx264", "mpeg4"):
        try:
            av.codec.Codec(name, "w")
            return name
        except Exception:  # noqa: BLE001,S112 - not in this FFmpeg build
            continue
    return "mpeg4"


def encode_video_note(frames: list[bytes], fps: float, pcm: bytes, path: str,
                      side: int = SIDE) -> float:
    """JPEG frames (square) + 16-bit mono PCM -> MP4 (H.264 + AAC). Returns the duration in
    seconds. Blocking."""
    import av

    with av.open(path, "w", format="mp4") as out:
        video = out.add_stream(_video_codec(), rate=round(fps))
        video.width = video.height = side
        video.pix_fmt = "yuv420p"
        video.bit_rate = 900_000
        audio = out.add_stream("aac", rate=RATE)
        audio.layout = "mono"
        audio.bit_rate = 64_000
        for index, data in enumerate(frames):
            pixels = _decode_jpeg(data)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24").reformat(
                format="yuv420p")
            frame.pts = index
            for packet in video.encode(frame):
                out.mux(packet)
        for packet in video.encode(None):
            out.mux(packet)
        samples = np.frombuffer(pcm, dtype=np.int16)
        if len(samples):
            chunk = 1024
            for start in range(0, len(samples), chunk):
                part = samples[start:start + chunk]
                frame = av.AudioFrame.from_ndarray(part.reshape(1, -1), format="s16",
                                                   layout="mono")
                frame.sample_rate = RATE
                frame.pts = start
                for packet in audio.encode(frame):
                    out.mux(packet)
            for packet in audio.encode(None):
                out.mux(packet)
    return max(len(frames) / fps, len(samples) / RATE if len(samples) else 0.0)


def _decode_jpeg(data: bytes) -> np.ndarray:
    """JPEG -> RGB array (QImage works in worker threads)."""
    image = QImage.fromData(data, "JPEG").convertToFormat(QImage.Format.Format_RGB888)
    height, width = image.height(), image.width()
    rows = np.frombuffer(image.constBits(), np.uint8).reshape(height, image.bytesPerLine())
    return rows[:, :width * 3].reshape(height, width, 3).copy()


_latest = [QImage()]  # the newest preview frame (one recording at a time per process)


class PreviewProvider(QQuickImageProvider):
    """image://camera/<n>: the latest camera frame, round. One per QML engine (the engine
    owns it); the frame itself is shared."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, image_id: str, size: QSize, requested_size: QSize) -> QImage:
        image = _latest[0]
        if image.isNull():
            return QImage()
        side = requested_size.width() if requested_size.width() > 0 else image.width()
        out = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, side, side))
        painter.setClipPath(path)
        painter.translate(side, 0)
        painter.scale(-1, 1)  # a mirror, like any selfie view
        painter.drawImage(QRectF(0, 0, side, side), image)
        painter.end()
        return out


class VideoNoteRecorder(QObject):
    changed = Signal()
    frameChanged = Signal()
    error = Signal(str)

    def __init__(self, client: TdClient, sink: VideoNoteSink, folder: Path,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._sink = sink
        self._folder = folder
        self._state = "idle"  # idle | recording | encoding
        self._camera: Any = None
        self._session: Any = None
        self._video_sink: Any = None
        self._audio: Any = None
        self._audio_device: Any = None
        self._frames: list[bytes] = []
        self._pcm = bytearray()
        self._frame_count = 0
        self._started = 0.0
        self._last_frame = 0.0
        self._last_action = 0.0
        self._chat_id = 0
        self._reply_to = 0
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        self._tasks: set[asyncio.Task[Any]] = set()
        _clean_old(folder)

    @Property(bool, notify=changed)
    def recording(self) -> bool:
        return self._state == "recording"

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._state != "idle"

    @Property(int, notify=changed)
    def seconds(self) -> int:
        return int(time.monotonic() - self._started) if self._state == "recording" else 0

    @Property(int, notify=frameChanged)
    def frame(self) -> int:
        """Bumps with every preview frame (QML reloads image://camera/<frame>)."""
        return self._frame_count

    @Slot("QVariant", "QVariant")
    def start(self, chat_id: Any, reply_to: Any = 0) -> None:
        if self._state != "idle" or not chat_id:
            return
        self._chat_id, self._reply_to = int(chat_id), int(reply_to or 0)
        self._ask("QCameraPermission", "camera",
                  lambda: self._ask("QMicrophonePermission", "microphone", self._begin))

    @Slot()
    def cancel(self) -> None:
        if self._state == "recording":
            self._stop()
            self._frames, self._pcm = [], bytearray()
            self._set_state("idle")
            self._action("chatActionCancel")

    @Slot()
    def finish(self) -> None:
        if self._state != "recording":
            return
        fps = len(self._frames) / max(0.001, time.monotonic() - self._started)
        self._stop()
        frames, pcm = self._frames, bytes(self._pcm)
        self._frames, self._pcm = [], bytearray()
        if len(frames) < MIN_SECONDS * 5:
            self._set_state("idle")
            self._action("chatActionCancel")
            return
        self._set_state("encoding")
        self._spawn(self._encode_and_send(frames, min(FPS, max(5.0, fps)), pcm,
                                          self._chat_id, self._reply_to))

    # --- internals --------------------------------------------------------------------------

    def _ask(self, kind: str, what: str, then: Any) -> None:
        from PySide6 import QtCore

        app = QtCore.QCoreApplication.instance()
        permission = getattr(QtCore, kind)()
        status = app.checkPermission(permission) if app else Qt.PermissionStatus.Granted
        if status == Qt.PermissionStatus.Granted:
            then()
        elif status == Qt.PermissionStatus.Denied:
            self.error.emit(f"No access to the {what}. Allow it in the system settings.")
        else:
            app.requestPermission(permission, self, lambda result: (
                then() if result.status() == Qt.PermissionStatus.Granted
                else self.error.emit(f"No access to the {what}.")))

    def _begin(self) -> None:
        from PySide6.QtMultimedia import (
            QAudioFormat,
            QAudioSource,
            QCamera,
            QMediaCaptureSession,
            QMediaDevices,
            QVideoSink,
        )

        camera_device = QMediaDevices.defaultVideoInput()
        if camera_device.isNull():
            self.error.emit("No camera found.")
            return
        self._camera = QCamera(camera_device, self)
        self._session = QMediaCaptureSession(self)
        self._session.setCamera(self._camera)
        self._video_sink = QVideoSink(self)
        self._session.setVideoSink(self._video_sink)
        self._video_sink.videoFrameChanged.connect(self._on_frame)
        audio_device = QMediaDevices.defaultAudioInput()
        if not audio_device.isNull():
            audio_format = QAudioFormat()
            audio_format.setSampleRate(RATE)
            audio_format.setChannelCount(1)
            audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if audio_device.isFormatSupported(audio_format):
                self._audio = QAudioSource(audio_device, audio_format, self)
                self._audio_device = self._audio.start()
                if self._audio_device is not None:
                    self._audio_device.readyRead.connect(self._read_audio)
        self._camera.start()
        self._frames, self._pcm = [], bytearray()
        self._started = time.monotonic()
        self._last_frame = 0.0
        self._last_action = 0.0
        self._set_state("recording")
        self._timer.start()
        self._tick()

    def _on_frame(self, frame: Any) -> None:
        if self._state != "recording":
            return
        now = time.monotonic()
        if now - self._last_frame < 1 / FPS - 0.004:
            return  # cameras often give 30 fps: keep ~25
        image = frame.toImage()
        if image.isNull():
            return
        self._last_frame = now
        self.add_frame(image)

    def add_frame(self, image: QImage) -> None:
        """A camera frame (also used by tests): kept for encoding, shown as the preview."""
        squared = square(image)
        self._frames.append(jpeg(squared))
        _latest[0] = squared
        self._frame_count += 1
        self.frameChanged.emit()

    def _read_audio(self) -> None:
        if self._audio_device is not None:
            self._pcm.extend(bytes(self._audio_device.readAll().data()))

    def _tick(self) -> None:
        self.changed.emit()
        now = time.monotonic()
        if now - self._last_action >= ACTION_INTERVAL:
            self._last_action = now
            self._action("chatActionRecordingVideoNote")
        if now - self._started >= MAX_SECONDS:
            self.finish()

    def _stop(self) -> None:
        self._timer.stop()
        if self._camera is not None:
            self._camera.stop()
            self._camera.deleteLater()
        if self._audio is not None:
            self._audio.stop()
            self._audio.deleteLater()
        for item in (self._session, self._video_sink):
            if item is not None:
                item.deleteLater()
        self._camera = self._session = self._video_sink = None
        self._audio = self._audio_device = None

    async def _encode_and_send(self, frames: list[bytes], fps: float, pcm: bytes,
                               chat_id: int, reply_to: int) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        path = self._folder / f"video-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        try:
            duration = await asyncio.to_thread(encode_video_note, frames, fps, pcm, str(path))
        except Exception as e:
            log.exception("Encoding a video message failed")
            self.error.emit(f"Could not encode the recording: {e}")
            self._set_state("idle")
            return
        self._set_state("idle")
        self._sink.send_video_note_to(chat_id, str(path), max(1, round(duration)), SIDE,
                                      reply_to)

    def _action(self, action: str) -> None:
        if self._chat_id:
            self._spawn(self._send_action(self._chat_id, action))

    async def _send_action(self, chat_id: int, action: str) -> None:
        try:
            await self._client.send({"@type": "sendChatAction", "chat_id": chat_id,
                                     "topic_id": None, "business_connection_id": "",
                                     "action": {"@type": action}})
        except TdError as e:
            log.debug("sendChatAction failed: %s", e)

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.changed.emit()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _clean_old(folder: Path) -> None:
    if not folder.is_dir():
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    for path in folder.glob("video-*.mp4"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
