"""File state for everything TDLib downloads or uploads (avatars, photos, voice, documents).

TDLib embeds file objects in chats and messages, but those snapshots go stale; updateFile is the
authority. So embedded objects are only *registered* (used if the file is unknown), while
updateFile and downloadFile results *update*.

A downloadFile response can be older than an updateFile that was dispatched before the awaiting
coroutine resumed (typical for small cached files: "downloading" response, then "completed"
update). The response is therefore applied only if no updateFile arrived since the request.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..td.client import Event, TdClient, TdError

log = logging.getLogger(__name__)

FileListener = Callable[[int], None]

AUTO_PRIORITY = 1    # previews the user is scrolling past
USER_PRIORITY = 32   # something the user clicked


@dataclass(frozen=True)
class FileState:
    id: int
    size: int = 0
    local_path: str = ""
    downloaded: int = 0
    downloading: bool = False
    completed: bool = False
    uploading: bool = False
    uploaded: int = 0

    @property
    def status(self) -> str:
        if self.completed and self.local_path:
            return "ready"
        if self.uploading:
            return "uploading"
        if self.downloading:
            return "downloading"
        return "remote"

    @property
    def progress(self) -> float:
        if not self.size:
            return 0.0
        done = self.uploaded if self.uploading else self.downloaded
        return max(0.0, min(1.0, done / self.size))


class FileManager:
    def __init__(self, client: TdClient, account: str = "0") -> None:
        self._client = client
        # Each TDLib instance numbers its files on its own: image URLs carry the account.
        self.account = account
        self._files: dict[int, FileState] = {}
        self._requested: set[int] = set()
        self._versions: dict[int, int] = {}  # file id -> number of updateFile events seen
        self._listeners: list[FileListener] = []
        # Tiny inline JPEG previews from TDLib, keyed by the file id they stand in for.
        self.minithumbnails: dict[int, bytes] = {}
        client.on("updateFile", self._on_update_file)

    def subscribe(self, listener: FileListener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def url(self, kind: str, file_id: int) -> str:
        """image://tg/<account>/<kind>/<file id> (see ui/images.py)."""
        return f"image://tg/{self.account}/{kind}/{file_id}"

    def get(self, file_id: int) -> FileState | None:
        return self._files.get(file_id)

    def path(self, file_id: int) -> str | None:
        state = self._files.get(file_id)
        return state.local_path if state and state.status == "ready" else None

    def register(self, file: dict[str, Any] | None) -> FileState | None:
        """Learn about a file from an embedded (possibly stale) object. Never overwrites."""
        if not file or "id" not in file:
            return None
        known = self._files.get(file["id"])
        if known is not None:
            return known
        state = _state(file)
        self._files[state.id] = state
        return state

    def update(self, file: dict[str, Any]) -> FileState:
        state = _state(file)
        previous = self._files.get(state.id)
        self._files[state.id] = state
        if state.completed or not state.downloading:
            self._requested.discard(state.id)
        if state != previous:
            for listener in list(self._listeners):
                try:
                    listener(state.id)
                except Exception:
                    log.exception("File listener failed")
        return state

    def download(self, file_id: int, priority: int = AUTO_PRIORITY) -> None:
        state = self._files.get(file_id)
        if (state and state.status == "ready") or file_id in self._requested:
            return
        self._requested.add(file_id)
        asyncio.ensure_future(self._download(file_id, priority))

    async def fetch(self, file_id: int, priority: int = USER_PRIORITY,
                    timeout: float = 120.0) -> str:
        """Download a file if needed and return its local path. Raises TimeoutError."""
        path = self.path(file_id)
        if path:
            return path
        ready: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        def on_file(changed: int) -> None:
            path = self.path(changed) if changed == file_id else None
            if path and not ready.done():
                ready.set_result(path)

        unsubscribe = self.subscribe(on_file)
        try:
            self.download(file_id, priority)
            return await asyncio.wait_for(ready, timeout)
        finally:
            unsubscribe()

    def cancel(self, file_id: int) -> None:
        self._requested.discard(file_id)
        asyncio.ensure_future(self._send_quietly(
            {"@type": "cancelDownloadFile", "file_id": file_id, "only_if_pending": False}))

    async def _download(self, file_id: int, priority: int) -> None:
        version = self._versions.get(file_id, 0)
        try:
            result = await self._client.send({
                "@type": "downloadFile", "file_id": file_id, "priority": priority,
                "offset": 0, "limit": 0, "synchronous": False,
            })
            if self._versions.get(file_id, 0) == version:
                self.update(result)  # otherwise an updateFile already brought newer state
        except TdError as e:
            log.debug("downloadFile(%s) failed: %s", file_id, e)
            self._requested.discard(file_id)

    async def _send_quietly(self, request: dict[str, Any]) -> None:
        try:
            await self._client.send(request)
        except TdError as e:
            log.debug("%s failed: %s", request["@type"], e)

    def _on_update_file(self, event: Event) -> None:
        file = event["file"]
        self._versions[file["id"]] = self._versions.get(file["id"], 0) + 1
        self.update(file)


def _state(file: dict[str, Any]) -> FileState:
    local = file.get("local") or {}
    remote = file.get("remote") or {}
    return FileState(
        id=file["id"],
        size=file.get("size") or file.get("expected_size") or 0,
        local_path=local.get("path", "") or "",
        downloaded=local.get("downloaded_size", 0) or 0,
        downloading=bool(local.get("is_downloading_active")),
        completed=bool(local.get("is_downloading_completed")),
        uploading=bool(remote.get("is_uploading_active")),
        uploaded=remote.get("uploaded_size", 0) or 0,
    )
