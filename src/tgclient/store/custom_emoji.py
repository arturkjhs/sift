"""Custom emoji (in message text and reactions): id -> sticker -> image. Qt-free.

Ids are collected while messages render and fetched in one getCustomEmojiStickers call per
event-loop turn (up to 200 ids each). The sticker file itself is downloaded (small); animated
ones (TGS/WebM) are drawn by their first frame (ui/images.py). Listeners get the ids whose
image became available, so views re-render only what changed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ..td.client import TdClient, TdError
from .files import AUTO_PRIORITY, FileManager

log = logging.getLogger(__name__)

BATCH = 200
Listener = Callable[[set[str]], None]


class CustomEmojiStore:
    def __init__(self, client: TdClient, files: FileManager) -> None:
        self._client = client
        self._files = files
        self.stickers: dict[str, dict[str, Any]] = {}  # id -> sticker
        self._file_ids: dict[int, set[str]] = {}  # sticker file id -> emoji ids
        self._wanted: set[str] = set()
        self._failed: set[str] = set()
        self._flush_scheduled = False
        self._listeners: list[Listener] = []
        files.subscribe(self._on_file)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def url(self, emoji_id: str) -> str | None:
        """Image URL if the emoji's picture is downloaded; otherwise starts getting it."""
        sticker = self.stickers.get(emoji_id)
        if sticker is None:
            self._want(emoji_id)
            return None
        file = sticker.get("sticker") or {}
        if not file.get("id"):
            return None
        if self._files.path(file["id"]):
            return self._files.url("sticker", file["id"])
        self._files.download(file["id"], AUTO_PRIORITY)
        return None

    def _want(self, emoji_id: str) -> None:
        if not emoji_id or emoji_id in self._wanted or emoji_id in self._failed:
            return
        self._wanted.add(emoji_id)
        if not self._flush_scheduled:
            self._flush_scheduled = True
            asyncio.get_event_loop().call_soon(lambda: asyncio.ensure_future(self._flush()))

    async def _flush(self) -> None:
        self._flush_scheduled = False
        wanted = sorted(self._wanted - set(self.stickers))
        self._wanted.clear()
        for start in range(0, len(wanted), BATCH):
            batch = wanted[start:start + BATCH]
            try:
                result = await self._client.send({
                    "@type": "getCustomEmojiStickers", "custom_emoji_ids": batch})
            except TdError as e:
                log.debug("getCustomEmojiStickers failed: %s", e)
                self._failed.update(batch)
                continue
            known: set[str] = set()
            for sticker in result.get("stickers") or []:
                emoji_id = str((sticker.get("full_type") or {}).get("custom_emoji_id", ""))
                if not emoji_id:
                    continue
                self.stickers[emoji_id] = sticker
                file = sticker.get("sticker") or {}
                if file.get("id"):
                    self._files.register(file)
                    self._file_ids.setdefault(file["id"], set()).add(emoji_id)
                    if self._files.path(file["id"]):
                        known.add(emoji_id)
                    else:
                        self._files.download(file["id"], AUTO_PRIORITY)
            self._failed.update(set(batch) - set(self.stickers))
            if known:
                self._emit(known)

    def _on_file(self, file_id: int) -> None:
        ids = self._file_ids.get(file_id)
        if ids and self._files.path(file_id):
            self._emit(set(ids))

    def _emit(self, ids: set[str]) -> None:
        for listener in list(self._listeners):
            try:
                listener(ids)
            except Exception:
                log.exception("Custom emoji listener failed")
