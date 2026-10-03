"""Chat summaries: collect a slice of history, build the prompt, turn citations into links.

The model sees each message as `[m<id>] <time> <sender>: <text>` and cites ids the same way;
`linkify` turns citations of ids that were really sent into
`tgc://message/<id>?t=<date>&s=<sender>` links (at most two per statement) and drops the ones
the model made up. When shown, `number_links` labels them 1, 2, 3… in reading order (the
renderer draws small superscript chips) and `link_tooltip` gives "Sender, <time>" with
`format.message_stamp` (15:52 / 01.10 15:52 / 01.10.25 15:52) as of the day of viewing.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import parse_qs, quote
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..store.format import content_preview, message_body, message_stamp
from ..store.richtext import utf16_index_map
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

Message = dict[str, Any]

SCOPES = ("unread", "day", "week")
MAX_MESSAGES = 1500
MAX_PERSON_MESSAGES = 800
MAX_CHARS = 200_000
PAGE = 100
LINK_SCHEME = "tgc://message/"
MAX_CITES = 2  # links per statement: more is noise

_ONE_CITATION = r"\[\s*m\d+(?:\s*[,;]\s*m\d+)*\s*\]"
# Adjacent citations ("[m1] [m2], [m3]") are one statement's sources: capped together.
_CITATIONS = re.compile(rf"[ \t]*{_ONE_CITATION}(?:[ \t]*[,;]?[ \t]*{_ONE_CITATION})*")
_MESSAGE_LINK = re.compile(
    r"\[[^\]\n]*\]\((" + re.escape(LINK_SCHEME) + r"[\d/-]+)\?t=(\d+)((?:&s=[^)\s]*)?)\)")
_HEADING_LINE = re.compile(r"^\s*#{1,6}\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

SYSTEM_PROMPT = """\
You summarize a Telegram chat for one of its participants (the reader), who hasn't read it.
The goal is conclusions, not a retelling.

Input: one message per line, formatted `[m<id>] <marks> <time> <sender>: <text>`. The marks
are set by the app and are reliable: `(you)` the reader's own message, `↩you` a reply to the
reader's message, `@you` the reader is mentioned. `(reply to m<id>)` before a text says which
message it answers.

Output markdown in {language}, whatever language the chat is in. Start directly with the
content: no introduction ("Here is a short overview…"), no closing remarks, no overall
impression of the chat.

1. The header line `For the reader:` lists the messages the app found to concern the reader
   (replies to their messages, mentions of them). If it lists any, begin with the heading
   `## {H:for_you}` and one bullet per listed message (merge ones about the same matter): what
   is asked or answered, and by whom, citing it. Nothing else goes into this section: a
   question to the whole chat is not addressed to the reader, however relevant it looks. If
   the line says "none", leave out this section and its heading entirely.
2. Then at most 7 topics, the most important first (not in chat order): decisions and
   agreements, events with date and place, questions left unanswered, practical takeaways.
   Each topic is one paragraph: `**<short title>.**` followed by 1–3 sentences. Lead with the
   conclusion or outcome, then the key facts. Name people only when the author matters (who
   organizes, who promised, who asked). Never retell who said what: no bullet or sentence
   per participant. If opinions differ, say it in one phrase, like "opinions split: A vs B"
   (in {language}).
- Skip noise (greetings, stickers, jokes, banter) unless it is most of the chat; then say so in
  one line.
- After every fact cite the messages it is based on as [m<id>] or [m<id>, m<id>]: at most 2,
  the most telling ones first. Use only ids that appear in the input.
- Length: about 120–250 words for a day of chat, proportionally less for a shorter period or a
  quiet chat; a week may take up to about 400.
- Do not invent facts.
"""


PERSON_PROMPT = """\
You write a short profile of one participant of a Telegram chat for another participant
(the reader), based only on that person's own messages in this chat.

Input: their messages, one per line, formatted `[m<id>] <time> <name>: <text>`.

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
- After every bullet cite the messages it is based on as [m<id>] or [m<id>, m<id>]: at most
  2, the most telling ones first. Use only ids that appear in the input.
- Only facts the person stated or that clearly follow from their messages. Do not guess,
  and do not infer sensitive traits (health, religion, ethnicity, sexuality, political views).
- No preamble, no closing remarks.
"""


@dataclass(frozen=True)
class Reader:
    """The user, for marking the lines that concern them. Done in code, not left to the model:
    own messages "(you)", replies to them "↩you", mentions "@you"."""
    user_id: int = 0
    usernames: tuple[str, ...] = ()  # without the @
    own_ids: frozenset[int] = frozenset()  # the user's messages, also ones replied to from outside


@dataclass(frozen=True)
class Source:
    """What the summary was built from (shown to the user, used for links)."""
    times: dict[int, int]  # citable id -> date of the message (unix), for its link label
    count: int
    truncated: bool
    # Cited id -> link target "<chat id>/<message id>" when results span chats (the cited
    # ids are then short references, not message ids). Empty: ids are messages of one chat.
    targets: dict[int, str] = field(default_factory=dict)
    senders: dict[int, str] = field(default_factory=dict)  # citable id -> sender, for tooltips
    # Messages that concern the reader (↩you, @you), picked in code: the only ones the
    # "for you" part of a chat summary may be about.
    for_you: tuple[int, ...] = ()


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
    now: datetime | None = None,
    reader: Reader | None = None,
) -> tuple[str, Source]:
    """Messages (oldest first) -> prompt text, lines marked for `reader` if given. Keeps the
    newest ones if it gets too long."""
    lines: list[tuple[int, str]] = []
    times: dict[int, int] = {}
    senders: dict[int, str] = {}
    concerning: set[int] = set()
    for message in messages:
        name = sender_name(message)
        line = message_line(message, name, transcript, now)
        if not line:
            continue
        marks = reader_marks(message, reader) if reader else ""
        if marks and marks != "(you)":
            concerning.add(message["id"])
        lines.append((message["id"], f"[m{message['id']}] {marks + ' ' if marks else ''}{line}"))
        times[message["id"]] = message.get("date", 0)
        senders[message["id"]] = name
    total = 0
    kept: list[tuple[int, str]] = []
    for message_id, line in reversed(lines):
        total += len(line) + 1
        if total > MAX_CHARS:
            break
        kept.append((message_id, line))
    kept.reverse()
    for_you = tuple(i for i, _ in kept if i in concerning)
    return "\n".join(line for _, line in kept), Source(
        times=times, count=len(kept), truncated=len(kept) < len(lines), senders=senders,
        for_you=for_you)


def _localize(text: str, language: str) -> str:
    from .assist import localize

    return localize(text, language)


def today_header(now: datetime | None = None) -> str:
    """First line of every prompt with message lines: what "today" is and how times read."""
    now = now or datetime.now()  # noqa: DTZ005
    return (f"Today: {now:%d.%m.%Y}, {_WEEKDAYS[now.weekday()]}. Message times: HH:MM is today, "
            "DD.MM HH:MM an earlier day this year, DD.MM.YY HH:MM an earlier year.")


def reader_marks(message: Message, reader: Reader) -> str:
    """"(you)", or "↩you" and/or "@you" for a message that concerns the reader."""
    if is_own(message, reader.user_id):
        return "(you)"
    marks = []
    if replied_id(message) in reader.own_ids:
        marks.append("↩you")
    if mentions(message, reader):
        marks.append("@you")
    return " ".join(marks)


def is_own(message: Message, user_id: int) -> bool:
    sender = message.get("sender_id") or {}
    return bool(message.get("is_outgoing")) or bool(
        user_id and sender.get("@type") == "messageSenderUser"
        and sender.get("user_id") == user_id)


def replied_id(message: Message) -> int:
    """The message this one replies to, if it is in the same chat; else 0."""
    reply = message.get("reply_to")
    if not isinstance(reply, dict) or reply.get("@type", "messageReplyToMessage") != (
            "messageReplyToMessage"):
        return 0
    if reply.get("chat_id") not in (None, 0, message.get("chat_id")):
        return 0
    return int(reply.get("message_id") or 0)


def mentions(message: Message, reader: Reader) -> bool:
    """A mention of the reader: by id (a name link) or by one of their @usernames."""
    ids, usernames = mentioned(message)
    return bool(reader.user_id and reader.user_id in ids) or bool(
        usernames & {u.lower() for u in reader.usernames})


def mentioned(message: Message) -> tuple[set[int], set[str]]:
    """Who the message mentions: user ids of name links, and @usernames (lowercase, no @)."""
    body = message_body(message.get("content") or {}) or {}
    text = body.get("text", "")
    ids: set[int] = set()
    usernames: set[str] = set()
    index = None
    for entity in body.get("entities") or []:
        kind = entity.get("type") or {}
        if kind.get("@type") == "textEntityTypeMentionName" and kind.get("user_id"):
            ids.add(int(kind["user_id"]))
        elif kind.get("@type") == "textEntityTypeMention":
            index = index or utf16_index_map(text)  # entity offsets are UTF-16
            start, end = entity.get("offset", 0), entity.get("offset", 0) + entity.get("length", 0)
            if end < len(index):
                usernames.add(text[index[start]:index[end]].lstrip("@").lower())
    return ids, usernames


def prompt(chat_title: str, reader: str, transcript_text: str,
           language: str = "en", period: str = "",
           for_you: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    listed = ", ".join(f"m{i}" for i in for_you) or "none"
    header = (f"{today_header()}\nChat: {chat_title}\nReader: {reader or 'unknown'}\n"
              + (f"Period: {period}\n" if period else "") + f"For the reader: {listed}\n\n")
    return [
        {"role": "system", "content": _localize(SYSTEM_PROMPT, language)},
        {"role": "user", "content": header + transcript_text},
    ]


def person_prompt(chat_title: str, name: str, reader: str,
                  transcript_text: str, language: str = "en") -> list[dict[str, Any]]:
    header = (f"{today_header()}\nChat: {chat_title}\nPerson: {name or 'unknown'}\n"
              f"Reader: {reader or 'unknown'}\n\n")
    return [
        {"role": "system", "content": _localize(PERSON_PROMPT, language)},
        {"role": "user", "content": header + transcript_text},
    ]


def linkify(text: str, source: Source) -> str:
    """[m12, m15] -> [](tgc://message/12?t=<date>&s=<sender>)[](tgc://message/15?…); unknown
    ids dropped, at most MAX_CITES per statement (adjacent citations count together). Labels
    are set when shown (`number_links`)."""

    def replace(match: re.Match[str]) -> str:
        links: list[str] = []
        for token in dict.fromkeys(re.findall(r"m(\d+)", match.group(0))):
            message_id = int(token)
            date = source.times.get(message_id)
            if date is None or len(links) >= MAX_CITES:
                continue
            target = source.targets.get(message_id, str(message_id))
            sender = source.senders.get(message_id, "")
            tail = f"&s={quote(sender, safe='')}" if sender else ""
            links.append(f"[]({LINK_SCHEME}{target}?t={int(date)}{tail})")
        return "".join(links)

    return re.sub(r"[ \t]+$", "", _CITATIONS.sub(replace, text), flags=re.MULTILINE)


def number_links(text: str) -> str:
    """Labels message links 1, 2, 3… in reading order; a message cited again keeps its number.
    Call it when showing the text (older summaries have time labels, they get numbers too)."""
    numbers: dict[str, int] = {}  # link target -> its number

    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        number = numbers.setdefault(target, len(numbers) + 1)
        return f"[{number}]({target}?t={match.group(2)}{match.group(3)})"

    return _MESSAGE_LINK.sub(replace, text)


def link_tooltip(link: str, now: datetime | None = None) -> str:
    """"Eugene, 02.10 19:56" for a message link (the time as of `now`); "" for other links."""
    if not link.startswith(LINK_SCHEME) or "?" not in link:
        return ""
    query = parse_qs(link.split("?", 1)[1])
    try:
        stamp = message_stamp(int(query["t"][0]), now or datetime.now())  # noqa: DTZ005
    except (KeyError, ValueError):
        return ""
    sender = query.get("s", [""])[0].strip()
    return f"{sender}, {stamp}" if sender else stamp


def keep_for_you(text: str, heading: str, ids: tuple[int, ...] | frozenset[int]) -> str:
    """The "for you" section (`## heading`) keeps only items that cite a message the code
    marked as concerning the reader; without any it goes, heading and all. Run before
    `linkify` (it reads the [m<id>] citations)."""
    wanted = {int(i) for i in ids}
    name = heading.strip().strip(".:").casefold()
    lines = text.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        found = _HEADING_LINE.match(lines[index])
        if not found or found.group(1).strip("*_ .:").casefold() != name:
            out.append(lines[index])
            index += 1
            continue
        index += 1
        items: list[list[str]] = []
        blank = False
        while index < len(lines):
            line = lines[index]
            if _HEADING_LINE.match(line):
                break
            if not line.strip():
                blank = True
            elif _ITEM.match(line) or not items:
                items.append([line])
                blank = False
            elif line[:1] in " \t" or not blank:
                items[-1].append(line)  # a wrapped item
            else:
                break  # a paragraph after the section: the first topic
            index += 1
        kept = [item for item in items if wanted & {
            int(i) for i in re.findall(r"\bm(\d+)\b", " ".join(re.findall(_ONE_CITATION,
                                                                       "\n".join(item))))}]
        if kept:
            out.append(found.group(0).strip())
            out.extend(line for item in kept for line in item)
            out.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


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
    parts = link[len(LINK_SCHEME):].split("?", 1)[0].split("/")
    try:
        if len(parts) == 1:
            return 0, int(parts[0])
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except ValueError:
        pass
    return 0, 0


def message_line(message: Message, sender: str,
                 transcript: Callable[[Message], str | None] = lambda m: None,
                 now: datetime | None = None) -> str:
    """'<time> <sender>: <text>' without the [m<id>] prefix; "" for empty messages."""
    text = _message_text(message, transcript)
    if not text:
        return ""
    return f"{message_stamp(message.get('date', 0), now)} {sender or 'Unknown'}: {text}"


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
