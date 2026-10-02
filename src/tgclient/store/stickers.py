"""Installed sticker sets and recently used stickers, for the sticker picker. Qt-free.

Loaded on demand (the first time the picker opens), kept current via updateInstalledStickerSets
and updateRecentStickers. Sticker objects are TDLib's `sticker`; their files go through
FileManager (register only: they are snapshots).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ..td.client import Event, TdClient, TdError
from .files import FileManager

log = logging.getLogger(__name__)

Sticker = dict[str, Any]
Listener = Callable[[str], None]  # "sets" | "recent" | "loaded" | "set:<id>"
REGULAR = {"@type": "stickerTypeRegular"}


class StickerStore:
    def __init__(self, client: TdClient, files: FileManager) -> None:
        self._client = client
        self._files = files
        self.sets: list[dict[str, Any]] = []  # stickerSetInfo, in the user's order
        self.recent: list[Sticker] = []
        self._stickers: dict[str, list[Sticker]] = {}  # set id (int64 as string) -> stickers
        self._loading: set[str] = set()
        self._loaded = False
        self._listeners: list[Listener] = []
        client.on("updateInstalledStickerSets", self._on_installed)
        client.on("updateRecentStickers", self._on_recent)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @property
    def loaded(self) -> bool:
        return self._loaded

    def stickers(self, set_id: str) -> list[Sticker] | None:
        """Stickers of a set if loaded; otherwise starts loading and returns None."""
        set_id = str(set_id)
        if set_id in self._stickers:
            return self._stickers[set_id]
        if set_id not in self._loading:
            self._loading.add(set_id)
            asyncio.ensure_future(self._load_set(set_id))
        return None

    async def load(self) -> None:
        """Installed sets and recent stickers. Safe to call repeatedly."""
        if self._loaded:
            return
        self._loaded = True
        await asyncio.gather(self._load_sets(), self._load_recent())
        self._emit("loaded")  # both lists known (they arrive in any order)

    async def _load_sets(self) -> None:
        try:
            result = await self._client.send(
                {"@type": "getInstalledStickerSets", "sticker_type": REGULAR})
        except TdError as e:
            log.warning("getInstalledStickerSets failed: %s", e)
            return
        self.sets = [s for s in result.get("sets") or [] if not s.get("is_archived")]
        for info in self.sets:
            for cover in info.get("covers") or []:
                self._register(cover)
        self._emit("sets")

    async def _load_recent(self) -> None:
        try:
            result = await self._client.send({"@type": "getRecentStickers", "is_attached": False})
        except TdError as e:
            log.warning("getRecentStickers failed: %s", e)
            return
        self.recent = list(result.get("stickers") or [])
        for sticker in self.recent:
            self._register(sticker)
        self._emit("recent")

    async def _load_set(self, set_id: str) -> None:
        try:
            result = await self._client.send({"@type": "getStickerSet", "set_id": set_id})
        except TdError as e:
            log.warning("getStickerSet(%s) failed: %s", set_id, e)
            self._loading.discard(set_id)
            return
        stickers = list(result.get("stickers") or [])
        for sticker in stickers:
            self._register(sticker)
        self._stickers[set_id] = stickers
        self._loading.discard(set_id)
        self._emit(f"set:{set_id}")

    def _register(self, sticker: Sticker) -> None:
        self._files.register(sticker.get("sticker"))
        thumbnail = (sticker.get("thumbnail") or {}).get("file")
        self._files.register(thumbnail)

    def _on_installed(self, event: Event) -> None:
        if (event.get("sticker_type") or {}).get("@type") == "stickerTypeRegular" and self._loaded:
            asyncio.ensure_future(self._load_sets())

    def _on_recent(self, event: Event) -> None:
        if not event.get("is_attached") and self._loaded:
            asyncio.ensure_future(self._load_recent())

    def _emit(self, what: str) -> None:
        for listener in list(self._listeners):
            try:
                listener(what)
            except Exception:
                log.exception("Sticker listener failed")
