"""Animated stickers and custom emoji in QML: TGS (Lottie, rendered by rlottie) and WebM
(VP9 with an alpha channel, decoded by libvpx through PyAV — FFmpeg's own VP9 decoder, which
Qt uses, drops the alpha).

Frames are rendered once per (file, pixel size) in a worker thread and kept in a shared
LRU cache, so a looping sticker costs CPU only on its first loop. `AnimatedImage` is the QML
item (registered as TgClient.Native/AnimatedImage); `first_frame` serves static previews.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import weakref
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

import shiboken6
from PySide6.QtCore import Property, QRectF, QTimer, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQml import qmlRegisterType
from PySide6.QtQuick import QQuickItem, QQuickPaintedItem

log = logging.getLogger(__name__)

MAX_FPS = 30  # Lottie stickers are 60 fps; every second frame looks the same in a chat
MAX_FRAMES = 180
CACHE_BYTES = 160 * 1024 * 1024
SIZE_STEP = 16  # pixel sizes are rounded up to share cached frames between similar items

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="frames")


def sniff(path: str) -> str:
    """tgs | webm | image (by content: TDLib file names carry no reliable extension)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return ""
    if head[:2] == b"\x1f\x8b":
        return "tgs"
    if head == b"\x1a\x45\xdf\xa3":
        return "webm"
    return "image"


@dataclass
class FrameSet:
    frames: list[QImage]
    fps: float

    @property
    def nbytes(self) -> int:
        return sum(f.sizeInBytes() for f in self.frames)


def load_frames(path: str, width: int, height: int, limit: int = MAX_FRAMES) -> FrameSet:
    """All frames of an animated sticker at (about) this size. Blocking: run in a thread."""
    kind = sniff(path)
    if kind == "tgs":
        return _tgs_frames(path, width, height, limit)
    if kind == "webm":
        return _webm_frames(path, width, height, limit)
    image = QImage(path)
    return FrameSet([image] if not image.isNull() else [], 0)


def first_frame(path: str, width: int, height: int) -> QImage:
    kind = sniff(path)
    if kind == "tgs":
        frames = _tgs_frames(path, width, height, 1)
    elif kind == "webm":
        frames = _webm_frames(path, width, height, 1)
    else:
        return QImage(path)
    return frames.frames[0] if frames.frames else QImage()


def _tgs_frames(path: str, width: int, height: int, limit: int) -> FrameSet:
    from rlottie_python import LottieAnimation

    animation = LottieAnimation.from_tgs(path)
    try:
        total = animation.lottie_animation_get_totalframe()
        fps = animation.lottie_animation_get_framerate() or 60.0
        step = max(1, round(fps / MAX_FPS))
        width, height = _fit(animation.lottie_animation_get_size(), width, height)
        frames = []
        for index in range(0, total, step):
            if len(frames) >= limit:
                break
            data = animation.lottie_animation_render(frame_num=index, width=width,
                                                     height=height)
            frames.append(QImage(data, width, height,
                                 QImage.Format.Format_ARGB32_Premultiplied).copy())
        return FrameSet(frames, fps / step)
    finally:
        animation.lottie_animation_destroy()


def _webm_frames(path: str, width: int, height: int, limit: int) -> FrameSet:
    import av

    frames: list[QImage] = []
    with av.open(path) as container:
        stream = container.streams.video[0]
        name = {"vp9": "libvpx-vp9", "vp8": "libvpx"}.get(stream.codec_context.name)
        context = av.CodecContext.create(name, "r") if name else stream.codec_context
        fps = float(stream.average_rate or stream.guessed_rate or 30)
        size = _fit((stream.codec_context.width, stream.codec_context.height), width, height)
        for packet in container.demux(stream):
            for frame in context.decode(packet):
                if len(frames) >= limit:
                    break
                rgba = frame.reformat(width=size[0], height=size[1], format="rgba")
                array = rgba.to_ndarray()
                image = QImage(array.data, size[0], size[1], array.strides[0],
                               QImage.Format.Format_RGBA8888)
                frames.append(image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))
            if len(frames) >= limit:
                break
    return FrameSet(frames, min(fps, 60.0))


def _fit(source: tuple[int, int], width: int, height: int) -> tuple[int, int]:
    sw, sh = source
    if sw <= 0 or sh <= 0:
        return width, height
    scale = min(width / sw, height / sh)
    return max(1, round(sw * scale)), max(1, round(sh * scale))


class FrameCache:
    def __init__(self, limit: int = CACHE_BYTES) -> None:
        self._limit = limit
        self._sets: OrderedDict[tuple[str, int, int], FrameSet] = OrderedDict()
        self._loading: dict[tuple[str, int, int], list[Callable[[FrameSet], None]]] = {}
        self._bytes = 0

    def get(self, path: str, width: int, height: int,
            callback: Callable[[FrameSet], None]) -> None:
        """Calls back on the event loop thread, right away if cached."""
        key = (path, width, height)
        cached = self._sets.get(key)
        if cached is not None:
            self._sets.move_to_end(key)
            callback(cached)
            return
        waiting = self._loading.get(key)
        if waiting is not None:
            waiting.append(callback)
            return
        self._loading[key] = [callback]
        future = asyncio.get_event_loop().run_in_executor(
            _executor, load_frames, path, width, height)
        future.add_done_callback(lambda f: self._loaded(key, f))

    def _loaded(self, key: tuple[str, int, int], future: asyncio.Future[FrameSet]) -> None:
        callbacks = self._loading.pop(key, [])
        try:
            frames = future.result()
        except Exception:
            log.exception("Rendering %s failed", key[0])
            frames = FrameSet([], 0)
        if frames.frames:
            self._sets[key] = frames
            self._bytes += frames.nbytes
            while self._bytes > self._limit and len(self._sets) > 1:
                _, old = self._sets.popitem(last=False)
                self._bytes -= old.nbytes
        for callback in callbacks:
            try:  # one broken receiver must not starve the others
                callback(frames)
            except Exception:
                log.exception("Delivering frames of %s failed", key[0])


cache = FrameCache()


class AnimatedImage(QQuickPaintedItem):
    """Plays an animated sticker file (TGS/WebM) in a loop while `playing`."""

    sourceChanged = Signal()
    playingChanged = Signal()
    readyChanged = Signal()

    def __init__(self, parent: QQuickItem | None = None) -> None:
        super().__init__(parent)
        self._source = ""
        self._playing = True
        self._frames: FrameSet | None = None
        self._index = 0
        self._key: tuple[str, int, int] | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.widthChanged.connect(self._reload)
        self.heightChanged.connect(self._reload)

    def _get_source(self) -> str:
        return self._source

    def _set_source(self, value: str) -> None:
        if value != self._source:
            self._source = value
            self.sourceChanged.emit()
            self._reload()

    def _get_playing(self) -> bool:
        return self._playing

    def _set_playing(self, value: bool) -> None:
        if value != self._playing:
            self._playing = value
            self.playingChanged.emit()
            self._sync_timer()

    def _get_ready(self) -> bool:
        return self._frames is not None and bool(self._frames.frames)

    source = Property(str, _get_source, _set_source, notify=sourceChanged)  # local file path
    playing = Property(bool, _get_playing, _set_playing, notify=playingChanged)
    ready = Property(bool, _get_ready, notify=readyChanged)

    def _reload(self) -> None:
        path = self._source.removeprefix("file://")
        ratio = self.window().devicePixelRatio() if self.window() else 1.0
        width = _bucket(self.width() * ratio)
        height = _bucket(self.height() * ratio)
        key = (path, width, height)
        if not path or width <= 0 or height <= 0 or key == self._key:
            return
        self._key = key
        # Rendering takes a moment in a worker thread; meanwhile the item may be destroyed
        # (scrolled out of the list, another chat opened). Keep only a weak reference and
        # check that the Qt object still exists before touching it.
        ref = weakref.ref(self)

        def deliver(frames: FrameSet) -> None:
            item = ref()
            if item is not None and shiboken6.isValid(item):
                item._set_frames(key, frames)

        cache.get(path, width, height, deliver)

    def _set_frames(self, key: tuple[str, int, int], frames: FrameSet) -> None:
        if key != self._key:
            return  # the source or size changed meanwhile
        was_ready = self._get_ready()
        self._frames = frames
        self._index = 0
        if self._get_ready() != was_ready:
            self.readyChanged.emit()
        self._sync_timer()
        self.update()

    def _sync_timer(self) -> None:
        frames = self._frames
        if self._playing and frames is not None and len(frames.frames) > 1 and frames.fps:
            self._timer.start(max(10, round(1000 / frames.fps)))
        else:
            self._timer.stop()

    def _advance(self) -> None:
        if self._frames and self._frames.frames:
            self._index = (self._index + 1) % len(self._frames.frames)
            self.update()

    def paint(self, painter: QPainter) -> None:
        if not self._frames or not self._frames.frames:
            return
        frame = self._frames.frames[min(self._index, len(self._frames.frames) - 1)]
        box = self.boundingRect()
        scale = min(box.width() / frame.width(), box.height() / frame.height())
        width, height = frame.width() * scale, frame.height() * scale
        target = QRectF(box.x() + (box.width() - width) / 2, box.y() + (box.height() - height) / 2,
                        width, height)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, frame)


def _bucket(value: float) -> int:
    return int(-(-int(value) // SIZE_STEP) * SIZE_STEP) if value > 0 else 0


_registered = False


def register_qml_types() -> None:
    global _registered
    if not _registered:
        qmlRegisterType(AnimatedImage, "TgClient.Native", 1, 0, "AnimatedImage")
        _registered = True
