"""Chat summaries: collect a slice of history, build the prompt, turn citations into links.

The model sees each message as `[m<id>] <time> <sender>: <text>` and cites ids the same way;
`linkify` turns citations of ids that were really sent into `tgc://message/<id>` links and drops
the ones the model made up.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..store.format import content_preview
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

Message = dict[str, Any]

SCOPES = ("unread", "day", "week")
MAX_MESSAGES = 1500
MAX_PERSON_MESSAGES = 800
MAX_CHARS = 200_000
PAGE = 100
LINK_SCHEME = "tgc://message/"

_CITATION = re.compile(r"\[\s*(m\d+(?:\s*[,;]\s*m\d+)*)\s*\]")

SYSTEM_PROMPT = """\
You summarize a Telegram chat for one of its participants, who hasn't read it.

Input: one message per line, formatted `[m<id>] <date time> <sender>: <text>`.

Rules:
- Write in {language}, whatever language the chat is in.
- Group by topic. Use short markdown bullet points, no preamble, no closing remarks.
- Put decisions, open questions, requests addressed to the reader, dates and deadlines first.
- After every bullet cite the messages it is based on as [m<id>] or [m<id>, m<id>].
  Use only ids that appear in the input. Cite at most 3 ids per bullet.
- Do not invent facts. If the chat is just small talk, say so in one line.
"""


PERSON_PROMPT = """\
You write a short profile of one participant of a Telegram chat for another participant
(the reader), based only on that person's own messages in this chat.

Input: their messages, one per line, formatted `[m<id>] <date time> <name>: <text>`.

Rules:
- Write in {language}, whatever language the messages are in.
- Markdown: `##` sections with short bullet points, headings written exactly as given (the
  part in parentheses says what goes in, never copy it); skip a section the messages say
  nothing about:
  ## {H:who} (role, work, place, background they mention)
  ## {H:topics}
  ## {H:knows}
  ## {H:plans} (with dates)
  ## {H:style}
- After every bullet cite the messages it is based on as [m<id>] or [m<id>, m<id>].
  Use only ids that appear in the input. Cite at most 3 ids per bullet.
- Only facts the person stated or that clearly follow from their messages. Do not guess,
  and do not infer sensitive traits (health, religion, ethnicity, sexuality, political views).
- No preamble, no closing remarks.
"""


@dataclass(frozen=True)
class Source:
    """What the summary was built from (shown to the user, used for links)."""
    times: dict[int, str]  # cited id -> label for its link ("HH:MM")
    count: int
    truncated: bool
    # Cited id -> link target "<chat id>/<message id>" when results span chats (the cited
    # ids are then short references, not message ids). Empty: ids are messages of one chat.
    targets: dict[int, str] = field(default_factory=dict)


def stop_condition(scope: str, last_read_inbox_id: int,
                   now: float | None = None) -> Callable[[Message], bool]:
    """Returns a predicate: True for the first message that is outside the scope."""
    if scope == "unread":
        return lambda m: m["id"] <= last_read_inbox_id
    seconds = 86_400 if scope == "day" else 7 * 86_400
    since = (now or time.time()) - seconds
    return lambda m: m.get("date", 0) < since


async def collect(client: TdClient, chat_id: int, outside: Callable[[Message], bool],
                  limit: int = MAX_MESSAGES) -> tuple[list[Message], bool]:
    """Newest-to-oldest paging until `outside` matches. Returns (oldest first, truncated)."""
    found: list[Message] = []
    from_id = 0
    while len(found) < limit:
        try:
            page = await client.send({
                "@type": "getChatHistory", "chat_id": chat_id, "from_message_id": from_id,
                "offset": 0, "limit": PAGE, "only_local": False,
            })
        except TdError as e:
            log.warning("getChatHistory for summary failed: %s", e)
            break
        batch = [m for m in page.get("messages") or [] if m and m["id"] != from_id]
        if not batch:
            break  # start of history
        for message in batch:
            if outside(message):
                found.reverse()
                return found, False
            found.append(message)
        from_id = batch[-1]["id"]
    found.reverse()
    return found[-limit:], len(found) >= limit


async def collect_from_sender(client: TdClient, chat_id: int, sender: str,
                              limit: int = MAX_PERSON_MESSAGES) -> tuple[list[Message], bool]:
    """The person's messages in this chat, newest `limit` of them. Returns (oldest first,
    truncated)."""
    sender_id = sender_object(sender)
    found: list[Message] = []
    from_id = 0
    while len(found) < limit:
        try:
            page = await client.send({
                "@type": "searchChatMessages", "chat_id": chat_id, "topic_id": None,
                "query": "", "sender_id": sender_id, "from_message_id": from_id, "offset": 0,
                "limit": PAGE, "filter": None,
            })
        except TdError as e:
            log.warning("searchChatMessages for a person summary failed: %s", e)
            break
        batch = [m for m in page.get("messages") or [] if m and m["id"] != from_id]
        if not batch:
            break
        found.extend(batch)
        from_id = page.get("next_from_message_id") or batch[-1]["id"]
    truncated = len(found) >= limit
    found = found[:limit]
    found.reverse()
    return found, truncated


def render(
    messages: list[Message],
    sender_name: Callable[[Message], str],
    transcript: Callable[[Message], str | None],
) -> tuple[str, Source]:
    """Messages (oldest first) -> prompt text. Keeps the newest ones if it gets too long."""
    lines: list[str] = []
    times: dict[int, str] = {}
    for message in messages:
        text = _message_text(message, transcript)
        if not text:
            continue
        stamp = datetime.fromtimestamp(message.get("date", 0))  # noqa: DTZ006
        sender = sender_name(message) or "Unknown"
        lines.append(f"[m{message['id']}] {stamp:%Y-%m-%d %H:%M} {sender}: {text}")
        times[message["id"]] = f"{stamp:%H:%M}"
    total = 0
    kept: list[str] = []
    for line in reversed(lines):
        total += len(line) + 1
        if total > MAX_CHARS:
            break
        kept.append(line)
    kept.reverse()
    return "\n".join(kept), Source(times=times, count=len(kept),
                                   truncated=len(kept) < len(lines))


def _localize(text: str, language: str) -> str:
    from .assist import localize

    return localize(text, language)


def prompt(chat_title: str, reader: str, transcript_text: str,
           language: str = "en") -> list[dict[str, Any]]:
    header = f"Chat: {chat_title}\nReader: {reader or 'unknown'}\n\n"
    return [
        {"role": "system", "content": _localize(SYSTEM_PROMPT, language)},
        {"role": "user", "content": header + transcript_text},
    ]


def person_prompt(chat_title: str, name: str, reader: str,
                  transcript_text: str, language: str = "en") -> list[dict[str, Any]]:
    header = f"Chat: {chat_title}\nPerson: {name or 'unknown'}\nReader: {reader or 'unknown'}\n\n"
    return [
        {"role": "system", "content": _localize(PERSON_PROMPT, language)},
        {"role": "user", "content": header + transcript_text},
    ]


def linkify(text: str, source: Source) -> str:
    """[m12, m15] -> [10:02](tgc://message/12) [10:05](tgc://message/15); unknown ids dropped."""

    def replace(match: re.Match[str]) -> str:
        links = []
        for token in re.split(r"\s*[,;]\s*", match.group(1)):
            message_id = int(token[1:])
            label = source.times.get(message_id)
            if label is not None:
                target = source.targets.get(message_id, str(message_id))
                links.append(f"[{label}]({LINK_SCHEME}{target})")
        return " ".join(links)

    linked = _CITATION.sub(replace, text)
    return re.sub(r"[ \t]+$", "", linked, flags=re.MULTILINE)


def sender_key(message: Message) -> str:
    sender = message.get("sender_id") or {}
    if sender.get("@type") == "messageSenderChat":
        return f"chat:{sender.get('chat_id', 0)}"
    return f"user:{sender.get('user_id', 0)}"


def sender_object(key: str) -> dict[str, Any] | None:
    kind, _, value = key.partition(":")
    if not value.lstrip("-").isdigit():
        return None
    if kind == "chat":
        return {"@type": "messageSenderChat", "chat_id": int(value)}
    return {"@type": "messageSenderUser", "user_id": int(value)}


def message_id_from_link(link: str) -> int:
    return parse_message_link(link)[1]


def parse_message_link(link: str) -> tuple[int, int]:
    """tgc://message/<id> -> (0, id): the open chat; tgc://message/<chat>/<id> -> (chat, id)."""
    if not link.startswith(LINK_SCHEME):
        return 0, 0
    parts = link[len(LINK_SCHEME):].split("/")
    try:
        if len(parts) == 1:
            return 0, int(parts[0])
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except ValueError:
        pass
    return 0, 0


def message_line(message: Message, sender: str,
                 transcript: Callable[[Message], str | None] = lambda m: None) -> str:
    """'<date time> <sender>: <text>' without the [m<id>] prefix; "" for empty messages."""
    text = _message_text(message, transcript)
    if not text:
        return ""
    stamp = datetime.fromtimestamp(message.get("date", 0))  # noqa: DTZ006
    return f"{stamp:%Y-%m-%d %H:%M} {sender or 'Unknown'}: {text}"


def message_text(message: Message,
                 transcript: Callable[[Message], str | None] = lambda m: None) -> str:
    """One-line text of a message as the model sees it (captions, transcripts, reply marks)."""
    return _message_text(message, transcript)


def _message_text(message: Message, transcript: Callable[[Message], str | None]) -> str:
    content = message.get("content", {})
    text = content_preview(content)
    if content.get("@type") == "messageVoiceNote":
        heard = transcript(message)
        if heard:
            text = f"Voice message: {heard}"
    text = " ".join(text.split())
    reply = message.get("reply_to")
    if text and isinstance(reply, dict) and reply.get("message_id"):
        text = f"(reply to m{reply['message_id']}) {text}"
    return text
