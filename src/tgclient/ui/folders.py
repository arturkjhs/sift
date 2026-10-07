"""Creating, editing, deleting and reordering chat folders. Exposed to QML as `folderEditor`.

A folder as QML edits it: {id, name, includeContacts, includeNonContacts, includeGroups,
includeChannels, includeBots, excludeMuted, excludeRead, excludeArchived, chats: [ids]}.
Fields TDLib has but the editor doesn't show (icon, colour, pinned and excluded chats) are kept
as they were.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from ..store.chats import MAIN, ChatStore
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

_FLAGS = {
    "includeContacts": "include_contacts", "includeNonContacts": "include_non_contacts",
    "includeGroups": "include_groups", "includeChannels": "include_channels",
    "includeBots": "include_bots", "excludeMuted": "exclude_muted",
    "excludeRead": "exclude_read", "excludeArchived": "exclude_archived",
}


class FolderEditor(QObject):
    loaded = Signal("QVariantMap")  # a folder to edit (or a new one)
    failed = Signal(str)

    def __init__(self, client: TdClient, chats: ChatStore, parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._chats = chats
        self._raw: dict[int, dict[str, Any]] = {}  # folder id -> TDLib chatFolder as loaded
        self._tasks: set[asyncio.Task[Any]] = set()

    @Slot(str)
    def edit(self, key: str) -> None:
        """Load a folder ("folder:<id>") for the editor; "" starts a new one."""
        if not key.startswith("folder:"):
            self.loaded.emit({"id": 0, "name": "", "chats": [],
                              **{flag: False for flag in _FLAGS}})
            return
        self._spawn(self._load(int(key.split(":", 1)[1])))

    @Slot("QVariantMap")
    def save(self, folder: dict[str, Any]) -> None:
        self._spawn(self._save(dict(folder)))

    @Slot(str)
    def remove(self, key: str) -> None:
        if key.startswith("folder:"):
            self._spawn(self._run({"@type": "deleteChatFolder",
                                   "chat_folder_id": int(key.split(":", 1)[1]),
                                   "leave_chat_ids": []}))

    @Slot(str, int)
    def move(self, key: str, delta: int) -> None:
        """Move a tab left (-1) or right (+1), "All chats" included."""
        keys = [f.key for f in self._chats.folders]
        if key not in keys:
            return
        index = keys.index(key)
        target = max(0, min(len(keys) - 1, index + delta))
        if target == index:
            return
        keys.insert(target, keys.pop(index))
        ids = [int(k.split(":", 1)[1]) for k in keys if k != MAIN]
        self._spawn(self._run({"@type": "reorderChatFolders", "chat_folder_ids": ids,
                               "main_chat_list_position": keys.index(MAIN)}))

    async def _load(self, folder_id: int) -> None:
        try:
            raw = await self._client.send({"@type": "getChatFolder",
                                           "chat_folder_id": folder_id})
        except TdError as e:
            self.failed.emit(e.message)
            return
        self._raw[folder_id] = raw
        name = raw.get("name") or {}
        title = (name.get("text") or {}).get("text", "") if isinstance(name, dict) else str(name)
        self.loaded.emit({"id": folder_id, "name": title,
                          "chats": [int(c) for c in raw.get("included_chat_ids") or []],
                          **{flag: bool(raw.get(field)) for flag, field in _FLAGS.items()}})

    async def _save(self, folder: dict[str, Any]) -> None:
        folder_id = int(folder.get("id") or 0)
        name = str(folder.get("name") or "").strip()[:12] or "Folder"  # Telegram's limit
        raw = dict(self._raw.get(folder_id) or {})
        chats = [int(c) for c in folder.get("chats") or []]
        flags = {field: bool(folder.get(flag)) for flag, field in _FLAGS.items()}
        if not chats and not any(v for k, v in flags.items() if k.startswith("include")):
            self.failed.emit("Add chats or chat types to the folder")
            return
        body = {
            "@type": "chatFolder", "icon": raw.get("icon"), "color_id": raw.get("color_id", -1),
            "is_shareable": bool(raw.get("is_shareable")),
            "pinned_chat_ids": [c for c in raw.get("pinned_chat_ids") or [] if c in chats],
            "excluded_chat_ids": raw.get("excluded_chat_ids") or [],
            **flags, "included_chat_ids": chats,
            "name": {"@type": "chatFolderName", "animate_custom_emoji": False,
                     "text": {"@type": "formattedText", "text": name, "entities": []}},
        }
        request = ({"@type": "editChatFolder", "chat_folder_id": folder_id, "folder": body}
                   if folder_id else {"@type": "createChatFolder", "folder": body})
        await self._run(request)

    async def _run(self, request: dict[str, Any]) -> None:
        try:
            await self._client.send(request)
        except TdError as e:
            log.warning("%s failed: %s", request["@type"], e)
            self.failed.emit(e.message)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
