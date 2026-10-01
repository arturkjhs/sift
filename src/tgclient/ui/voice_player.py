"""One player for voice messages (and audio files): click to download if needed, then play."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from ..store.files import USER_PRIORITY, FileManager
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)


class VoicePlayer(QObject):
    stateChanged = Signal()
    positionChanged = Signal()

    def __init__(self, client: TdClient, files: FileManager, parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._files = files
        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._player.setAudioOutput(self._output)
        self._file_id = 0
        self._waiting_for = 0
        self._message: tuple[int, int] = (0, 0)
        self._player.playbackStateChanged.connect(lambda _s: self.stateChanged.emit())
        self._player.positionChanged.connect(lambda _p: self.positionChanged.emit())
        self._player.durationChanged.connect(lambda _d: self.positionChanged.emit())
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(
            lambda _e, text: log.warning("Voice playback failed: %s", text))
        files.subscribe(self._on_file)

    # --- QML API ----------------------------------------------------------------------------

    @Property("QVariant", notify=stateChanged)
    def fileId(self) -> int:
        return self._file_id

    @Property(bool, notify=stateChanged)
    def playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @Property(bool, notify=stateChanged)
    def loading(self) -> bool:
        return self._waiting_for != 0

    @Property(float, notify=positionChanged)
    def progress(self) -> float:
        duration = self._player.duration()
        return self._player.position() / duration if duration > 0 else 0.0

    @Property(int, notify=positionChanged)
    def positionSeconds(self) -> int:
        return self._player.position() // 1000

    @Slot("QVariant", "QVariant", "QVariant")
    def toggle(self, file_id: Any, chat_id: Any, message_id: Any) -> None:
        file_id = int(file_id or 0)
        if not file_id:
            return
        if file_id == self._file_id and not self._waiting_for:
            if self.playing:
                self._player.pause()
            else:
                self._player.play()
            return
        self.stop()
        self._file_id = file_id
        self._message = (int(chat_id or 0), int(message_id or 0))
        path = self._files.path(file_id)
        if path:
            self._start(path)
        else:
            self._waiting_for = file_id
            self._files.download(file_id, USER_PRIORITY)
        self.stateChanged.emit()

    @Slot(float)
    def seek(self, fraction: float) -> None:
        duration = self._player.duration()
        if duration > 0:
            self._player.setPosition(int(max(0.0, min(1.0, fraction)) * duration))

    @Slot()
    def stop(self) -> None:
        self._player.stop()
        self._player.setSource(QUrl())
        self._file_id = 0
        self._waiting_for = 0
        self.stateChanged.emit()
        self.positionChanged.emit()

    # --- internals --------------------------------------------------------------------------

    def _start(self, path: str) -> None:
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()
        chat_id, message_id = self._message
        if chat_id and message_id:  # marks the voice message as listened
            asyncio.ensure_future(self._open_content(chat_id, message_id))

    async def _open_content(self, chat_id: int, message_id: int) -> None:
        try:
            await self._client.send(
                {"@type": "openMessageContent", "chat_id": chat_id, "message_id": message_id})
        except TdError as e:
            log.debug("openMessageContent failed: %s", e)

    def _on_file(self, file_id: int) -> None:
        if file_id == self._waiting_for:
            path = self._files.path(file_id)
            if path:
                self._waiting_for = 0
                self._start(path)
                self.stateChanged.emit()

    def _on_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.stop()
