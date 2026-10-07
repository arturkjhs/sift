"""Contacts: the list (searchable), opening a chat with one, adding by phone number or from a
profile, removing. Exposed to QML as `contacts`."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..store.files import AUTO_PRIORITY
from ..store.format import initials
from ..store.presence import PresenceStore, status_text
from ..store.users import UserStore
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

AVATAR_COLORS = 7


class ContactsModel(QObject):
    changed = Signal()
    chatReady = Signal("QVariant")  # a private chat to open
    failed = Signal(str)

    def __init__(self, client: TdClient, users: UserStore, files: Any,
                 presence: PresenceStore | None = None, parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._users = users
        self._files = files
        self._presence = presence
        self._ids: list[int] = []
        self._query = ""
        self._busy = False
        self._tasks: set[asyncio.Task[Any]] = set()
        users.subscribe(self._on_user)
        files.subscribe(self._on_file)

    @Property("QVariantList", notify=changed)
    def rows(self) -> list[dict[str, Any]]:
        """{userId, name, status, online, avatar, initials, colorIndex}, online first."""
        rows = []
        for user_id in self._ids:
            user = self._users.users.get(user_id)
            if user is None:
                continue
            status = self._presence.statuses.get(user_id) if self._presence else None
            online = bool(self._presence and self._presence.is_online(user_id))
            avatar = ""
            if user.photo_file_id is not None:
                if self._files.path(user.photo_file_id):
                    avatar = self._files.url("avatar", user.photo_file_id)
                else:
                    self._files.download(user.photo_file_id, AUTO_PRIORITY)
            rows.append({"userId": user_id, "name": user.full_name or user.phone,
                         "status": status_text(status), "online": online, "avatar": avatar,
                         "initials": initials(user.full_name),
                         "colorIndex": user_id % AVATAR_COLORS})
        rows.sort(key=lambda r: (not r["online"], r["name"].casefold()))
        return rows

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Slot(str)
    def search(self, query: str) -> None:
        """"" lists all contacts."""
        self._query = query.strip()
        self._spawn(self._load(self._query))

    @Slot("QVariant")
    def openChat(self, user_id: Any) -> None:
        self._spawn(self._open(int(user_id or 0)))

    @Slot(str, str, str)
    def add(self, phone: str, first_name: str, last_name: str) -> None:
        """A new contact by phone number (Telegram finds the account, if there is one)."""
        self._spawn(self._import(phone.strip(), first_name.strip(), last_name.strip()))

    @Slot("QVariant", bool)
    def addFromProfile(self, user_id: Any, share_phone: bool) -> None:
        user = self._users.users.get(int(user_id or 0))
        if user is None:
            return
        self._spawn(self._run({
            "@type": "addContact", "user_id": user.id, "share_phone_number": share_phone,
            "contact": {"@type": "importedContact", "phone_number": user.phone,
                        "first_name": user.first_name, "last_name": user.last_name,
                        "note": None}}))

    @Slot("QVariant")
    def remove(self, user_id: Any) -> None:
        self._spawn(self._run({"@type": "removeContacts", "user_ids": [int(user_id or 0)]}))

    # --- internals --------------------------------------------------------------------------

    async def _load(self, query: str) -> None:
        self._busy = True
        self.changed.emit()
        try:
            found = await self._client.send({"@type": "searchContacts", "query": query,
                                             "limit": 500} if query else {"@type": "getContacts"})
        except TdError as e:
            log.info("Contacts failed: %s", e)
            found = {}
        self._busy = False
        if query == self._query:
            self._ids = [int(i) for i in found.get("user_ids") or []]
        self.changed.emit()

    async def _open(self, user_id: int) -> None:
        try:
            chat = await self._client.send({"@type": "createPrivateChat", "user_id": user_id,
                                            "force": False})
        except TdError as e:
            self.failed.emit(e.message)
            return
        self.chatReady.emit(chat["id"])

    async def _import(self, phone: str, first_name: str, last_name: str) -> None:
        if not phone or not first_name:
            self.failed.emit("A phone number and a first name are needed")
            return
        try:
            result = await self._client.send({"@type": "importContacts", "contacts": [{
                "@type": "importedContact", "phone_number": phone, "first_name": first_name,
                "last_name": last_name, "note": None}]})
        except TdError as e:
            self.failed.emit(e.message)
            return
        user_ids = [i for i in result.get("user_ids") or [] if i]
        if not user_ids:
            self.failed.emit("This number isn't on Telegram")
            return
        await self._load(self._query)
        await self._open(int(user_ids[0]))

    async def _run(self, request: dict[str, Any]) -> None:
        try:
            await self._client.send(request)
        except TdError as e:
            self.failed.emit(e.message)
            return
        await self._load(self._query)

    def _on_user(self, user_id: int) -> None:
        if user_id in self._ids:
            self.changed.emit()

    def _on_file(self, file_id: int) -> None:
        if any((u := self._users.users.get(i)) and u.photo_file_id == file_id
               for i in self._ids):
            self.changed.emit()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
