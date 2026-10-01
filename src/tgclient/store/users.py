"""Minimal user cache (names), used for sender names in chat previews."""

from __future__ import annotations

from dataclasses import dataclass

from ..td.client import Event, TdClient


@dataclass
class User:
    id: int
    first_name: str
    last_name: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class UserStore:
    def __init__(self, client: TdClient) -> None:
        self.users: dict[int, User] = {}
        self.my_id: int | None = None
        client.on("updateUser", self._on_user)
        client.on("updateOption", self._on_option)

    def _on_user(self, event: Event) -> None:
        raw = event["user"]
        self.users[raw["id"]] = User(
            id=raw["id"],
            first_name=raw.get("first_name", ""),
            last_name=raw.get("last_name", ""),
        )

    def _on_option(self, event: Event) -> None:
        if event.get("name") == "my_id":
            value = event.get("value", {})
            if value.get("@type") == "optionValueInteger":
                self.my_id = int(value["value"])  # int64 -> string in JSON
