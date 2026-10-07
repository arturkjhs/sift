"""Picking a sender for a "From:" search filter. Exposed to QML as `senderPicker`.

In a group: its members (searchChatMembers, as the @mention suggestions do); in a private
chat: the other person and me; with no chat (the global search): known users by name or
username. Rows: {key: "user:<id>", name, username, avatar, initials, colorIndex}."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..store.chats import ChatStore
from ..store.files import AUTO_PRIORITY
from ..store.format import initials
from ..store.users import User, UserStore
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

AVATAR_COLORS = 7
LIMIT = 12


class SenderPicker(QObject):
    changed = Signal()

    def __init__(self, client: TdClient, chats: ChatStore, users: UserStore,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._chats = chats
        self._users = users
        self._rows: list[dict[str, Any]] = []
        self._task: asyncio.Task[Any] | None = None

    @Property("QVariantList", notify=changed)
    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows)

    @Slot(str, "QVariant")
    def find(self, text: str, chat_id: Any) -> None:
        if self._task is not None:
            self._task.cancel()
        self._task = asyncio.ensure_future(self._find(text.strip().lstrip("@"),
                                                      int(chat_id or 0)))

    async def _find(self, text: str, chat_id: int) -> None:
        chat = self._chats.chats.get(chat_id)
        needle = text.lower()
        users: list[User] = []
        if chat is not None and chat.type in ("group", "supergroup"):
            try:
                found = await self._client.send({"@type": "searchChatMembers",
                                                 "chat_id": chat_id, "query": text,
                                                 "limit": LIMIT, "filter": None})
            except TdError as e:
                log.info("searchChatMembers failed: %s", e)
                found = {}
            for member in found.get("members") or []:
                user = self._users.users.get((member.get("member_id") or {}).get("user_id", 0))
                if user is not None:
                    users.append(user)
        elif chat is not None and chat.type in ("private", "secret"):
            for user_id in (chat.peer_id, self._users.my_id or 0):
                user = self._users.users.get(user_id)
                if user is not None and _matches(user, needle):
                    users.append(user)
        else:
            users = [u for u in self._users.users.values()
                     if not u.is_bot and needle and _matches(u, needle)]
            users.sort(key=lambda u: (not u.is_contact, u.full_name.lower()))
        self._rows = [self._row(u) for u in users[:LIMIT]]
        self.changed.emit()

    def _row(self, user: User) -> dict[str, Any]:
        avatar = ""
        files = self._chats.files
        if user.photo_file_id is not None:
            if files.path(user.photo_file_id):
                avatar = files.url("avatar", user.photo_file_id)
            else:
                files.download(user.photo_file_id, AUTO_PRIORITY)
        name = user.full_name or (user.usernames[0] if user.usernames else str(user.id))
        return {"key": f"user:{user.id}", "name": name,
                "username": user.usernames[0] if user.usernames else "",
                "avatar": avatar, "initials": initials(name),
                "colorIndex": user.id % AVATAR_COLORS}


def _matches(user: User, needle: str) -> bool:
    if not needle:
        return True
    return needle in user.full_name.lower() or any(needle in u.lower() for u in user.usernames)
