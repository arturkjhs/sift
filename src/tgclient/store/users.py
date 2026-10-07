"""Minimal user cache: names and profile photos (sender names and avatars)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from ..td.client import Event, TdClient
from .files import FileManager


@dataclass
class User:
    id: int
    first_name: str
    last_name: str
    photo_file_id: int | None = None  # small profile photo; FileManager knows its state
    usernames: tuple[str, ...] = ()  # active @usernames, without the @
    is_bot: bool = False
    is_verified: bool = False
    phone: str = ""
    is_contact: bool = False
    is_premium: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


log = logging.getLogger(__name__)


class UserStore:
    def __init__(self, client: TdClient, files: FileManager | None = None) -> None:
        self._files = files
        self.users: dict[int, User] = {}
        self.my_id: int | None = None
        self._listeners: list[Callable[[int], None]] = []
        client.on("updateUser", self._on_user)
        client.on("updateOption", self._on_option)

    def subscribe(self, listener: Callable[[int], None]) -> Callable[[], None]:
        """Called with a user id when a user (or my_id) changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _emit(self, user_id: int) -> None:
        for listener in list(self._listeners):
            try:
                listener(user_id)
            except Exception:
                log.exception("User listener failed")

    @property
    def me(self) -> User | None:
        return self.users.get(self.my_id or 0)

    def _on_user(self, event: Event) -> None:
        raw = event["user"]
        small = (raw.get("profile_photo") or {}).get("small")
        if small and self._files is not None:
            self._files.register(small)
        self.users[raw["id"]] = User(
            id=raw["id"],
            first_name=raw.get("first_name", ""),
            last_name=raw.get("last_name", ""),
            photo_file_id=small["id"] if small else None,
            usernames=tuple((raw.get("usernames") or {}).get("active_usernames") or ()),
            is_bot=(raw.get("type") or {}).get("@type") == "userTypeBot",
            is_verified=bool((raw.get("verification_status") or {}).get("is_verified")
                             or raw.get("is_verified")),
            phone=raw.get("phone_number", ""),
            is_contact=bool(raw.get("is_contact")),
            is_premium=bool(raw.get("is_premium")),
        )
        self._emit(raw["id"])

    def _on_option(self, event: Event) -> None:
        if event.get("name") == "my_id":
            value = event.get("value", {})
            if value.get("@type") == "optionValueInteger":
                self.my_id = int(value["value"])  # int64 -> string in JSON
                self._emit(self.my_id)
