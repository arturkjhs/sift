"""Who is online and who is typing: user statuses, chat actions, member counts. Qt-free.

TDLib cancels chat actions itself (an updateChatAction with chatActionCancel ~6 s after the
last one), so no timers here. Status texts depend on the current time: callers refresh them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from ..td.client import Event, TdClient

log = logging.getLogger(__name__)

PresenceKind = Literal["user", "typing", "members", "online_count"]
Listener = Callable[[PresenceKind, Any], None]

# Present-continuous phrases; "" means the action isn't shown.
_ACTIONS = {
    "chatActionTyping": "typing",
    "chatActionRecordingVoiceNote": "recording a voice message",
    "chatActionUploadingVoiceNote": "sending a voice message",
    "chatActionRecordingVideo": "recording a video",
    "chatActionUploadingVideo": "sending a video",
    "chatActionRecordingVideoNote": "recording a video message",
    "chatActionUploadingVideoNote": "sending a video message",
    "chatActionUploadingPhoto": "sending a photo",
    "chatActionUploadingDocument": "sending a file",
    "chatActionChoosingSticker": "choosing a sticker",
    "chatActionChoosingLocation": "choosing a location",
    "chatActionChoosingContact": "choosing a contact",
    "chatActionStartPlayingGame": "playing a game",
}


@dataclass
class Typing:
    sender_key: int  # user id or chat id of the sender
    action: str  # TDLib chatAction @type


class PresenceStore:
    def __init__(self, client: TdClient) -> None:
        self.statuses: dict[int, dict[str, Any]] = {}  # user id -> TDLib UserStatus
        self.bots: set[int] = set()
        self.typing: dict[int, list[Typing]] = {}  # chat id -> current actions, oldest first
        self.members: dict[int, int] = {}  # basic group / supergroup id -> member count
        self.channels: set[int] = set()  # supergroup ids that are channels
        self.online_count: dict[int, int] = {}  # chat id -> online members (open chats only)
        # basic group / supergroup id -> my ChatMemberStatus there
        self.group_status: dict[int, dict[str, Any]] = {}
        self.verified: set[int] = set()  # supergroup ids with a verified badge
        self._listeners: list[Listener] = []
        handlers: dict[str, Callable[[Event], None]] = {
            "updateUser": self._on_user,
            "updateUserStatus": self._on_status,
            "updateChatAction": self._on_action,
            "updateChatOnlineMemberCount": self._on_online_count,
            "updateBasicGroup": self._on_basic_group,
            "updateSupergroup": self._on_supergroup,
        }
        for update_type, handler in handlers.items():
            client.on(update_type, handler)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def is_online(self, user_id: int, now: float | None = None) -> bool:
        status = self.statuses.get(user_id) or {}
        return (status.get("@type") == "userStatusOnline"
                and status.get("expires", 0) > (now or time.time()))

    def knows_status(self, chat: Any) -> bool:
        return chat.peer_id in self.group_status

    def member_status(self, chat: Any) -> str:
        """My ChatMemberStatus @type in a group/channel ("" if unknown)."""
        status = self.group_status.get(chat.peer_id) or {}
        kind = status.get("@type", "")
        if kind in ("chatMemberStatusCreator", "chatMemberStatusRestricted") and not status.get(
                "is_member", True):
            return "chatMemberStatusLeft"
        return kind

    def can_post(self, chat: Any) -> bool:
        """Channels: only the owner and admins who may post."""
        status = self.group_status.get(chat.peer_id) or {}
        kind = status.get("@type", "")
        if kind == "chatMemberStatusCreator":
            return True
        if kind == "chatMemberStatusAdministrator":
            return bool((status.get("rights") or {}).get("can_post_messages"))
        return False

    def _emit(self, kind: PresenceKind, payload: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:
                log.exception("Presence listener failed")

    def _on_user(self, event: Event) -> None:
        user = event["user"]
        if (user.get("type") or {}).get("@type") == "userTypeBot":
            self.bots.add(user["id"])
        if user.get("status"):
            self.statuses[user["id"]] = user["status"]
            self._emit("user", user["id"])

    def _on_status(self, event: Event) -> None:
        self.statuses[event["user_id"]] = event["status"]
        self._emit("user", event["user_id"])

    def _on_action(self, event: Event) -> None:
        chat_id = event["chat_id"]
        sender = event.get("sender_id") or {}
        key = int(sender.get("user_id") or sender.get("chat_id") or 0)
        action = (event.get("action") or {}).get("@type", "chatActionCancel")
        current = [t for t in self.typing.get(chat_id, []) if t.sender_key != key]
        if action != "chatActionCancel" and _ACTIONS.get(action):
            current.append(Typing(key, action))
        if current:
            self.typing[chat_id] = current
        else:
            self.typing.pop(chat_id, None)
        self._emit("typing", chat_id)

    def _on_online_count(self, event: Event) -> None:
        self.online_count[event["chat_id"]] = event.get("online_member_count", 0)
        self._emit("online_count", event["chat_id"])

    def _on_basic_group(self, event: Event) -> None:
        group = event["basic_group"]
        self.members[group["id"]] = group.get("member_count", 0)
        if group.get("status"):
            self.group_status[group["id"]] = group["status"]
        self._emit("members", group["id"])

    def _on_supergroup(self, event: Event) -> None:
        group = event["supergroup"]
        self.members[group["id"]] = group.get("member_count", 0)
        if (group.get("verification_status") or {}).get("is_verified") or group.get(
                "is_verified"):
            self.verified.add(group["id"])
        else:
            self.verified.discard(group["id"])
        if group.get("status"):
            self.group_status[group["id"]] = group["status"]
        if group.get("is_channel"):
            self.channels.add(group["id"])
        self._emit("members", group["id"])


def typing_text(typing: list[Typing], name_of: Callable[[int], str], private: bool) -> str:
    """'typing…', 'Olena is typing…', 'Olena and Petr are typing…', '3 people are typing…'."""
    if not typing:
        return ""
    phrase = _ACTIONS.get(typing[-1].action, "typing")
    if private:
        return f"{phrase}…"
    names = [name_of(t.sender_key).split(" ")[0] or "Someone" for t in typing]
    if len(names) == 1:
        return f"{names[0]} is {phrase}…"
    if len(names) == 2:
        if all(t.action == typing[0].action for t in typing):
            return f"{names[0]} and {names[1]} are {phrase}…"
        return f"{names[0]} and {names[1]} are typing…"
    return f"{len(names)} people are typing…"


def status_text(status: dict[str, Any] | None, now: datetime | None = None) -> str:
    """'online', 'last seen 5 minutes ago', 'last seen yesterday at 21:40', ..."""
    now = now or datetime.now()  # noqa: DTZ005 - local time by design
    kind = (status or {}).get("@type", "userStatusEmpty")
    if kind == "userStatusOnline":
        expires = status.get("expires", 0)  # type: ignore[union-attr]
        if expires > now.timestamp():
            return "online"
        return _last_seen(expires, now)
    if kind == "userStatusOffline":
        return _last_seen(status.get("was_online", 0), now)  # type: ignore[union-attr]
    return {
        "userStatusRecently": "last seen recently",
        "userStatusLastWeek": "last seen within a week",
        "userStatusLastMonth": "last seen within a month",
    }.get(kind, "last seen a long time ago")


def _last_seen(timestamp: int, now: datetime) -> str:
    if not timestamp:
        return "last seen recently"
    when = datetime.fromtimestamp(timestamp)  # noqa: DTZ006
    minutes = int((now - when).total_seconds() // 60)
    if minutes < 1:
        return "last seen just now"
    if minutes < 60:
        return "last seen 1 minute ago" if minutes == 1 else f"last seen {minutes} minutes ago"
    if when.date() == now.date():
        return f"last seen at {when:%H:%M}"
    if now.date() - when.date() == timedelta(days=1):
        return f"last seen yesterday at {when:%H:%M}"
    if when.year == now.year:
        return f"last seen {when:%d.%m} at {when:%H:%M}"
    return f"last seen {when:%d.%m.%y}"


def members_text(count: int, online: int, channel: bool) -> str:
    if count <= 0:
        return ""
    if channel:
        return "1 subscriber" if count == 1 else f"{count} subscribers"
    text = "1 member" if count == 1 else f"{count} members"
    if online > 1:
        text += f", {online} online"
    return text
