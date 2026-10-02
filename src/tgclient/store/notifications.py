"""New-message notifications from TDLib's notification API. Qt-free.

TDLib decides what deserves a notification (chat and scope mute settings, mentions and replies
in muted chats, silent messages) and withdraws notifications once their messages are read.
It only sends notification updates when the option `notification_group_count_max` is above 0,
so `start()` sets it. A platform backend (ui/notifications.py) implements NotificationSink.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..td.client import Event, TdClient, TdError
from .chats import ChatStore
from .format import content_preview
from .users import UserStore

log = logging.getLogger(__name__)

GROUP_COUNT_MAX = 25
# A burst (e.g. messages that arrived while the app was closed) shows at most this many.
BURST_LIMIT = 5
BURST_SECONDS = 3.0
_BODY_LIMIT = 200
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Notice:
    key: str  # "<group id>:<notification id>", unique per TDLib instance
    chat_id: int
    message_id: int
    title: str  # chat title
    subtitle: str  # sender name in groups, "" otherwise
    body: str
    silent: bool


class NotificationSink(Protocol):
    def show(self, notice: Notice) -> None: ...
    def withdraw(self, keys: list[str]) -> None: ...


class Notifier:
    def __init__(
        self, client: TdClient, chats: ChatStore, users: UserStore, sink: NotificationSink,
        suppress: Callable[[int], bool] = lambda chat_id: False,
    ) -> None:
        self._client = client
        self._chats = chats
        self._users = users
        self.sink = sink
        self.suppress = suppress  # e.g. the chat is open in the focused window
        self.enabled = True
        self.show_preview = True
        # Smart notifications: chats where a model decides whether a message deserves one.
        self.smart: Callable[[int], bool] = lambda chat_id: False
        self.relevance: Callable[[int, int], Awaitable[bool]] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._shown: set[str] = set()
        self._recent: list[float] = []  # monotonic times of recently shown notifications
        client.on("updateNotificationGroup", self._on_group)

    async def start(self) -> None:
        try:
            await self._client.send({
                "@type": "setOption", "name": "notification_group_count_max",
                "value": {"@type": "optionValueInteger", "value": GROUP_COUNT_MAX},
            })
        except TdError as e:
            log.warning("Notifications unavailable: %s", e)

    def withdraw_all(self) -> None:
        if self._shown:
            self.sink.withdraw(sorted(self._shown))
            self._shown.clear()

    def _on_group(self, event: Event) -> None:
        group_id = event["notification_group_id"]
        removed = [f"{group_id}:{n}" for n in event.get("removed_notification_ids", [])]
        removed = [key for key in removed if key in self._shown]
        if removed:
            self._shown.difference_update(removed)
            self.sink.withdraw(removed)
        # Several messages of one chat in one update: only the newest pops up.
        added = event.get("added_notifications", [])[-1:]
        for notification in added:
            notice = self._notice(group_id, event.get("chat_id", 0), notification)
            if notice is None or not self.enabled or self.suppress(notice.chat_id):
                continue
            if self.relevance is not None and self.smart(notice.chat_id):
                task = asyncio.ensure_future(self._show_if_relevant(notice))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                continue
            self._show(notice)

    async def _show_if_relevant(self, notice: Notice) -> None:
        assert self.relevance is not None
        if await self.relevance(notice.chat_id, notice.message_id):
            if self.enabled and not self.suppress(notice.chat_id):
                self._show(notice)
        else:
            log.debug("Smart notifications: skipped %s", notice.key)

    def _show(self, notice: Notice) -> None:
        if not self._within_burst_limit():
            return
        self._shown.add(notice.key)
        try:
            self.sink.show(notice)
        except Exception:
            log.exception("Showing a notification failed")

    def _within_burst_limit(self) -> bool:
        now = time.monotonic()
        self._recent = [t for t in self._recent if now - t < BURST_SECONDS]
        if len(self._recent) >= BURST_LIMIT:
            return False
        self._recent.append(now)
        return True

    def _notice(self, group_id: int, chat_id: int, notification: dict[str, Any]) -> Notice | None:
        kind = notification.get("type") or {}
        if kind.get("@type") != "notificationTypeNewMessage":
            return None  # secret chat started, calls: not handled yet
        message = kind.get("message") or {}
        chat_id = message.get("chat_id") or chat_id
        chat = self._chats.chats.get(chat_id)
        title = chat.title if chat else ""
        preview = (self.show_preview and kind.get("show_preview", True)
                   and not (chat and chat.type == "secret"))
        subtitle = ""
        if chat and chat.type in ("group", "supergroup"):
            subtitle = self._sender_name(message)
        if preview:
            body = _WS.sub(" ", content_preview(message.get("content", {}))).strip()
            if len(body) > _BODY_LIMIT:
                body = body[: _BODY_LIMIT - 1] + "…"
        else:
            body, subtitle = "New message", ""
        return Notice(
            key=f"{group_id}:{notification['id']}",
            chat_id=chat_id,
            message_id=message.get("id", 0),
            title=title,
            subtitle=subtitle,
            body=body or "New message",
            silent=bool(notification.get("is_silent")),
        )

    def _sender_name(self, message: dict[str, Any]) -> str:
        sender = message.get("sender_id") or {}
        if sender.get("@type") == "messageSenderUser":
            user = self._users.users.get(sender.get("user_id", 0))
            return user.full_name if user else ""
        chat = self._chats.chats.get(sender.get("chat_id", 0))
        return chat.title if chat else ""
