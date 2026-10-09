"""Pure formatting helpers for chat list rows (no Qt, easy to test)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .chats import Chat
from .users import UserStore

_WS = re.compile(r"\s+")
_PREVIEW_LIMIT = 120


def _text(formatted: Any) -> str:
    if isinstance(formatted, dict):
        return formatted.get("text", "")
    return formatted or ""


def _with_caption(label: str, content: dict[str, Any]) -> str:
    caption = _text(content.get("caption"))
    return f"{label}, {caption}" if caption else label


def content_preview(content: dict[str, Any]) -> str:
    match content.get("@type"):
        case "messageText":
            return _text(content.get("text"))
        case "messagePhoto":
            return _with_caption("Photo", content)
        case "messageVideo":
            return _with_caption("Video", content)
        case "messageAnimation":
            return _with_caption("GIF", content)
        case "messageDocument":
            name = content.get("document", {}).get("file_name") or "File"
            return _with_caption(name, content)
        case "messageAudio":
            audio = content.get("audio", {})
            return _with_caption(audio.get("title") or audio.get("file_name") or "Audio", content)
        case "messageVoiceNote":
            return _with_caption("Voice message", content)
        case "messageVideoNote":
            return "Video message"
        case "messageSticker":
            emoji = content.get("sticker", {}).get("emoji", "")
            return f"{emoji} Sticker".strip()
        case "messageAnimatedEmoji" | "messageDice":
            return content.get("emoji", "")
        case "messageLocation" | "messageVenue":
            return "Location"
        case "messageContact":
            return "Contact"
        case "messagePoll":
            return f"Poll: {_text(content.get('poll', {}).get('question'))}"
        case "messageCall":
            return "Call"
        case "messageChatAddMembers" | "messageChatJoinByLink" | "messageChatJoinByRequest":
            return "joined the group"
        case "messageChatDeleteMember":
            return "left the group"
        case "messagePinMessage":
            return "pinned a message"
        case "messageChatChangeTitle":
            return "changed the group name"
        case "messageChatChangePhoto":
            return "changed the group photo"
        case None:
            return ""
        case other:
            return other.removeprefix("message")


_SERVICE_TYPES = {
    "messageBasicGroupChatCreate", "messageSupergroupChatCreate", "messagePinMessage",
    "messageScreenshotTaken", "messageContactRegistered", "messageForumTopicCreated",
    "messageForumTopicEdited", "messageForumTopicIsClosedToggled", "messageGiftedPremium",
    "messageCustomServiceAction", "messageExpiredPhoto", "messageExpiredVideo",
}


def is_service(content: dict[str, Any]) -> bool:
    content_type = content.get("@type", "")
    return content_type.startswith("messageChat") or content_type in _SERVICE_TYPES


def media_label(content: dict[str, Any]) -> str:
    """Label for non-text content shown above the caption ("Photo", "Voice message", ...)."""
    if content.get("@type") in ("messageText", "messageAnimatedEmoji", None):
        return ""  # an animated emoji is its sticker or, without one, the emoji as text
    label = content_preview({k: v for k, v in content.items() if k != "caption"})
    return label


def message_body(content: dict[str, Any]) -> dict[str, Any] | None:
    """The formattedText to render in the bubble: text for text messages, caption otherwise."""
    if content.get("@type") == "messageText":
        return content.get("text")
    if content.get("@type") == "messageAnimatedEmoji":
        # Shown as a sticker when TDLib has one; otherwise the bubble shows the emoji itself.
        if (content.get("animated_emoji") or {}).get("sticker"):
            return None
        return {"@type": "formattedText", "text": content.get("emoji", ""), "entities": []}
    caption = content.get("caption")
    return caption if isinstance(caption, dict) and caption.get("text") else None


def day_label(timestamp: int, now: datetime | None = None) -> str:
    when = datetime.fromtimestamp(timestamp).date()  # noqa: DTZ006 - local time by design
    today = (now or datetime.now()).date()  # noqa: DTZ005
    if when == today:
        return "Today"
    if today - when == timedelta(days=1):
        return "Yesterday"
    if when.year == today.year:
        return f"{when.day} {when.strftime('%B')}"
    return f"{when.day} {when.strftime('%B')} {when.year}"


def clock(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")  # noqa: DTZ006


def message_preview(chat: Chat, users: UserStore) -> str:
    message = chat.last_message
    if not message:
        return ""
    text = _WS.sub(" ", content_preview(message.get("content", {}))).strip()
    if len(text) > _PREVIEW_LIMIT:
        text = text[: _PREVIEW_LIMIT - 1] + "…"

    if chat.type in ("group", "supergroup"):
        if message.get("is_outgoing"):
            return f"You: {text}"
        sender = message.get("sender_id", {})
        if sender.get("@type") == "messageSenderUser":
            user = users.users.get(sender["user_id"])
            if user and user.first_name:
                return f"{user.first_name}: {text}"
    return text


def message_time(chat: Chat, now: datetime | None = None) -> str:
    message = chat.last_message
    if not message or not message.get("date"):
        return ""
    return short_time(message["date"], now)


def short_time(timestamp: int, now: datetime | None = None) -> str:
    """Today: 14:05; this week: Mon; this year: 03.02; older: 03.02.24."""
    # Display in the user's local time zone on purpose.
    when = datetime.fromtimestamp(timestamp)  # noqa: DTZ006
    now = now or datetime.now()  # noqa: DTZ005
    if when.date() == now.date():
        return when.strftime("%H:%M")
    if now.date() - when.date() < timedelta(days=7):
        return when.strftime("%a")
    if when.year == now.year:
        return when.strftime("%d.%m")
    return when.strftime("%d.%m.%y")


def message_stamp(timestamp: int, now: datetime | None = None) -> str:
    """Time of a message in AI text, links and model input, no words (any language):
    today 15:52; earlier this year 01.10 15:52; other years 01.10.25 15:52. Local time."""
    when = datetime.fromtimestamp(timestamp)  # noqa: DTZ006 - local time by design
    now = now or datetime.now()  # noqa: DTZ005
    if when.date() == now.date():
        return when.strftime("%H:%M")
    if when.year == now.year:
        return when.strftime("%d.%m %H:%M")
    return when.strftime("%d.%m.%y %H:%M")


def initials(title: str) -> str:
    words = [w for w in title.split() if w[:1].isalnum()]
    if not words:
        return title[:1].upper() if title else "?"
    letters = words[0][0] + (words[1][0] if len(words) > 1 else "")
    return letters.upper()
