"""Recording voice messages: the microphone through QtMultimedia (raw PCM), encoded to
Ogg/Opus with PyAV (Qt's FFmpeg build has no Opus encoder; Telegram voice notes are Opus).

QML: start() → `recording`, `seconds`, `level`; finish() encodes in a worker thread and sends
through MessageListModel.send_voice(); cancel() drops it. While recording, the chat sees
"recording a voice message" (chatActionRecordingVoiceNote).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ..store.media import encode_waveform
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)


class VoiceSink(Protocol):
    def send_voice_to(self, chat_id: int, path: str, duration: int, waveform: bytes,
                      reply_to: int = 0) -> None: ...

RATE = 48_000
MIN_SECONDS = 0.7  # shorter: an accidental click
MAX_SECONDS = 30 * 60
WAVEFORM_SAMPLES = 100
ACTION_INTERVAL = 5.0
KEEP_DAYS = 7  # recordings must outlive their upload (TDLib reads the file later)


def encode_ogg_opus(pcm: bytes, path: str, rate: int = RATE) -> float:
    """16-bit mono PCM -> Ogg/Opus file; returns the duration in seconds. Blocking."""
    import av

    samples = np.frombuffer(pcm, dtype=np.int16)
    with av.open(path, "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=rate)
        stream.layout = "mono"
        stream.bit_rate = 32_000
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return len(samples) / rate


def waveform(pcm: bytes, samples: int = WAVEFORM_SAMPLES) -> bytes:
    """Telegram's waveform: `samples` peaks scaled to 0..31, packed 5 bits each."""
    data = np.abs(np.frombuffer(pcm, dtype=np.int16).astype(np.int32))
    if not len(data):
        return encode_waveform([0] * samples)
    chunks = np.array_split(data, samples)
    peaks = np.array([int(c.max()) if len(c) else 0 for c in chunks], dtype=np.float64)
    top = peaks.max() or 1.0
    return encode_waveform([round(31 * p / top) for p in peaks])


class VoiceRecorder(QObject):
    changed = Signal()
    levelChanged = Signal()
    error = Signal(str)

    def __init__(self, client: TdClient, sink: VoiceSink, folder: Path,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._sink = sink
        self._folder = folder
        self._source: Any = None  # QAudioSource
        self._device: Any = None  # its QIODevice
        self._pcm = bytearray()
        self._level = 0.0
        self._state = "idle"  # idle | recording | encoding
        self._started = 0.0
        self._chat_id = 0
        self._reply_to = 0
        self._last_action = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(200)
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

    @Property(float, notify=levelChanged)
    def level(self) -> float:
        """Input loudness 0..1 for the UI."""
        return self._level

    @Slot("QVariant", "QVariant")
    def start(self, chat_id: Any, reply_to: Any = 0) -> None:
        if self._state != "idle" or not chat_id:
            return
        self._chat_id, self._reply_to = int(chat_id), int(reply_to or 0)
        self._with_permission(self._begin)

    @Slot()
    def cancel(self) -> None:
        if self._state == "recording":
            self._stop()
            self._pcm = bytearray()
            self._set_state("idle")
            self._action("chatActionCancel")

    @Slot()
    def finish(self) -> None:
        if self._state != "recording":
            return
        self._stop()
        pcm = bytes(self._pcm)
        self._pcm = bytearray()
        if len(pcm) / 2 / RATE < MIN_SECONDS:
            self._set_state("idle")
            self._action("chatActionCancel")
            return
        self._set_state("encoding")
        self._spawn(self._encode_and_send(pcm, self._chat_id, self._reply_to))

    # --- internals --------------------------------------------------------------------------

    def _with_permission(self, then: Any) -> None:
        from PySide6.QtCore import QCoreApplication, QMicrophonePermission, Qt

        app = QCoreApplication.instance()
        permission = QMicrophonePermission()
        status = app.checkPermission(permission) if app else Qt.PermissionStatus.Granted
        if status == Qt.PermissionStatus.Granted:
            then()
        elif status == Qt.PermissionStatus.Denied:
            self.error.emit("No access to the microphone. Allow it in the system settings.")
        else:
            app.requestPermission(permission, self, lambda result: (
                then() if result.status() == Qt.PermissionStatus.Granted
                else self.error.emit("No access to the microphone.")))

    def _begin(self) -> None:
        from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            self.error.emit("No microphone found.")
            return
        audio_format = QAudioFormat()
        audio_format.setSampleRate(RATE)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(audio_format):
            self.error.emit("The microphone doesn't support 48 kHz mono recording.")
            return
        self._source = QAudioSource(device, audio_format, self)
        self._device = self._source.start()
        if self._device is None:
            self.error.emit("Could not start recording.")
            return
        self._device.readyRead.connect(self._read)
        self._pcm = bytearray()
        self._started = time.monotonic()
        self._last_action = 0.0
        self._set_state("recording")
        self._timer.start()
        self._tick()

    def _read(self) -> None:
        if self._device is None:
            return
        chunk = bytes(self._device.readAll().data())
        if not chunk:
            return
        self._pcm.extend(chunk)
        samples = np.frombuffer(chunk[: len(chunk) // 2 * 2], dtype=np.int16)
        if len(samples):
            peak = float(np.abs(samples.astype(np.int32)).max()) / 32768
            self._level = max(peak, self._level * 0.7)  # quick attack, slow decay
            self.levelChanged.emit()

    def _tick(self) -> None:
        self.changed.emit()  # seconds
        now = time.monotonic()
        if now - self._last_action >= ACTION_INTERVAL:
            self._last_action = now
            self._action("chatActionRecordingVoiceNote")
        if now - self._started > MAX_SECONDS:
            self.finish()

    def _stop(self) -> None:
        self._timer.stop()
        if self._source is not None:
            self._source.stop()
            self._source.deleteLater()
        self._source = self._device = None
        self._level = 0.0
        self.levelChanged.emit()

    async def _encode_and_send(self, pcm: bytes, chat_id: int, reply_to: int) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        path = self._folder / f"voice-{time.strftime('%Y%m%d-%H%M%S')}.ogg"
        try:
            duration = await asyncio.to_thread(encode_ogg_opus, pcm, str(path))
            wave = await asyncio.to_thread(waveform, pcm)
        except Exception as e:
            log.exception("Encoding a voice message failed")
            self.error.emit(f"Could not encode the recording: {e}")
            self._set_state("idle")
            return
        self._set_state("idle")
        self._sink.send_voice_to(chat_id, str(path), max(1, round(duration)), wave, reply_to)

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
    for path in folder.glob("voice-*.ogg"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
