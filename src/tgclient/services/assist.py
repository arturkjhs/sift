"""Prompts and parsers for the M8 assistant features. Qt-free, no I/O.

Everything here builds on summary.py's convention: the model sees messages as
`[m<id>] <date time> <sender>: <text>` and cites them as [m<id>]; `summary.linkify` turns the
citations into links. Results that span chats use short references (m1, m2, ...) whose link
targets carry the chat id (summary.Source.targets).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from . import summary
from .summary import Message, Source

LANGUAGES = {"uk": "Ukrainian", "ru": "Russian", "cs": "Czech", "en": "English"}
NATIVE = {"uk": "Українська", "ru": "Русский", "cs": "Čeština", "en": "English"}  # for Settings
TONES = {
    "neutral": "neutral and natural",
    "friendly": "warm and friendly",
    "formal": "polite and formal",
    "brief": "as short as possible, one sentence",
}

CITE_RULE = ("After every statement cite the messages it is based on as [m<id>] or "
             "[m<id>, m<id>]. Use only ids that appear in the input. Cite at most 3 ids.")


# Bump when the explain / reply prompts change: their cache is keyed by the message, not by
# the prompt text, so old answers would otherwise come back.
PROMPT_VERSION = 2

# Fixed words of AI output (section headings, table headers, set phrases) in each language.
# Given to the model ready-made: asked to translate an English heading, it often copied the
# English instead (with the prompt's explanation after a dash).
L10N: dict[str, dict[str, str]] = {
    "uk": {"gist": "Суть", "want": "Чого від тебе хочуть", "context": "Контекст",
           "tone": "Тон", "unclear": "Що незрозуміло", "who": "Хто це",
           "topics": "Теми й інтереси", "knows": "У чому розбирається",
           "plans": "Плани, обіцянки й прохання", "style": "Як спілкується",
           "nothing": "Нічого важливого", "no_answer": "Без відповіді", "person": "Хто",
           "answer": "Відповідь", "source": "Джерело",
           "classes": "Так / Ні / Можливо / Інше",
           "no_events": "Домовлених дат і зустрічей не знайдено."},
    "ru": {"gist": "Суть", "want": "Чего от тебя хотят", "context": "Контекст",
           "tone": "Тон", "unclear": "Что неясно", "who": "Кто это",
           "topics": "Темы и интересы", "knows": "В чём разбирается",
           "plans": "Планы, обещания и просьбы", "style": "Как общается",
           "nothing": "Ничего важного", "no_answer": "Без ответа", "person": "Кто",
           "answer": "Ответ", "source": "Источник", "classes": "Да / Нет / Может быть / Другое",
           "no_events": "Договорённых дат и встреч не найдено."},
    "cs": {"gist": "Podstata", "want": "Co od tebe chtějí", "context": "Kontext",
           "tone": "Tón", "unclear": "Co je nejasné", "who": "Kdo to je",
           "topics": "Témata a zájmy", "knows": "V čem se vyzná",
           "plans": "Plány, sliby a prosby", "style": "Jak komunikuje",
           "nothing": "Nic důležitého", "no_answer": "Bez odpovědi", "person": "Kdo",
           "answer": "Odpověď", "source": "Zdroj", "classes": "Ano / Ne / Možná / Jiné",
           "no_events": "Žádná domluvená data ani schůzky."},
    "en": {"gist": "Gist", "want": "What they want from you", "context": "Context",
           "tone": "Tone", "unclear": "Unclear", "who": "Who they are",
           "topics": "Topics and interests", "knows": "What they know well",
           "plans": "Plans, promises and requests", "style": "How they communicate",
           "nothing": "Nothing important", "no_answer": "No answer", "person": "Person",
           "answer": "Answer", "source": "Source", "classes": "Yes / No / Maybe / Other",
           "no_events": "No agreed dates or meetings found."},
}


def word(code: str, key: str) -> str:
    return L10N.get(code, L10N["en"])[key]


def _lang(prompt: str, code: str) -> str:
    """Prompts say {language} where the user's language goes (Settings) and {H:<key>} where a
    fixed word of the output goes, already in that language."""
    words = L10N.get(code, L10N["en"])
    prompt = re.sub(r"\{H:(\w+)\}", lambda m: words[m.group(1)], prompt)
    return prompt.replace("{language}", LANGUAGES.get(code, "English"))


localize = _lang

_HEADING_TAIL = re.compile(r"^(#{1,6}\s+[^—–:(]+?)\s*(?:[—–:]|\s-\s|\().*$")


def clean_headings(text: str) -> str:
    """Drop explanations a model copies after a heading ("## Tone — only if it stands out")."""
    return "\n".join(_HEADING_TAIL.sub(r"\1", line) if line.startswith("#") else line
                     for line in text.splitlines())


def system_user(system: str, user: str) -> list[dict[str, Any]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# --- translation ----------------------------------------------------------------------------

def translate_prompt(text: str, lang: str) -> list[dict[str, Any]]:
    target = LANGUAGES.get(lang, "English")
    return system_user(
        f"Translate the user's message into {target}. Keep the meaning, tone, names, emoji, "
        "links and markdown markup (**bold**, __italic__, `code`). If it is already in "
        f"{target}, return it unchanged. Output only the translation, no quotes, no notes.",
        text)


# --- questions about a chat -----------------------------------------------------------------

ASK_PROMPT = f"""\
You answer a question about a Telegram chat for one of its participants (the reader), using
only the chat messages in the input (found by search, so they may be out of order or partial).

Input: the question, then messages formatted `[m<id>] <date time> <sender>: <text>`.

Rules:
- Answer in {{language}}. Be brief: a sentence or a few bullet points.
- {CITE_RULE}
- If the messages don't contain the answer, say so plainly and don't guess.
"""


def ask_prompt(chat_title: str, reader: str, question: str, lines: str,
               language: str = "en") -> list[dict[str, Any]]:
    return system_user(_lang(ASK_PROMPT, language), f"Chat: {chat_title}\nReader: {reader or 'unknown'}\n"
                                   f"Question: {question}\n\nMessages:\n{lines}")


# --- dates, meetings, .ics ------------------------------------------------------------------

EVENTS_PROMPT = """\
You extract agreed dates, meetings, deadlines and appointments from a Telegram chat.

Input: messages formatted `[m<id>] <date time> <sender>: <text>`. Resolve relative dates
("tomorrow", "next Friday") against the date of the message that mentions them.

Output only JSON, no code fences: {"events": [{"title": "...", "start": "YYYY-MM-DDTHH:MM" or
"YYYY-MM-DD" for all-day, "end": same format or null, "location": "..." or "", "notes":
"one short line" or "", "ref": "m<id>"}]}. Include only events that were actually agreed or
announced (not vague ideas), each once, with the message where it was settled as "ref".
Write titles and notes in {language}. Empty list if there are none.
"""


def events_prompt(chat_title: str, lines: str, language: str = "en") -> list[dict[str, Any]]:
    return system_user(_lang(EVENTS_PROMPT, language), f"Chat: {chat_title}\n\n{lines}")


@dataclass(frozen=True)
class Event:
    title: str
    start: str  # YYYY-MM-DDTHH:MM or YYYY-MM-DD
    end: str = ""
    location: str = ""
    notes: str = ""
    ref: int = 0  # message id (or cross-chat reference) where it was agreed

    @property
    def all_day(self) -> bool:
        return "T" not in self.start


def parse_json(reply: str) -> Any:
    """The model's JSON, tolerating code fences and text around it."""
    text = reply.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


def parse_events(reply: str) -> list[Event]:
    try:
        data = parse_json(reply)
    except ValueError:
        return []
    items = data.get("events", []) if isinstance(data, dict) else data
    events = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        start = _date_text(item.get("start"))
        if not start or not item.get("title"):
            continue
        ref = str(item.get("ref") or "").lstrip("m")
        events.append(Event(
            title=str(item["title"]).strip(), start=start, end=_date_text(item.get("end")),
            location=str(item.get("location") or "").strip(),
            notes=str(item.get("notes") or "").strip(),
            ref=int(ref) if ref.isdigit() else 0))
    events.sort(key=lambda e: e.start)
    return events


def _date_text(value: Any) -> str:
    text = str(value or "").strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            when = datetime.strptime(text, fmt)  # noqa: DTZ007 - local, floating times
        except ValueError:
            continue
        return when.strftime("%Y-%m-%d" if fmt == "%Y-%m-%d" else "%Y-%m-%dT%H:%M")
    return ""


def events_markdown(events: list[Event], language: str = "en") -> str:
    if not events:
        return word(language, "no_events")
    lines = []
    for event in events:
        when = _when_label(event)
        extra = f", {event.location}" if event.location else ""
        notes = f" — {event.notes}" if event.notes else ""
        cite = f" [m{event.ref}]" if event.ref else ""
        lines.append(f"- **{event.title}** · {when}{extra}{notes}{cite}")
    return "\n".join(lines)


def _when_label(event: Event) -> str:
    if event.all_day:
        return datetime.strptime(event.start, "%Y-%m-%d").strftime("%a %d %b %Y")  # noqa: DTZ007
    start = datetime.strptime(event.start, "%Y-%m-%dT%H:%M")  # noqa: DTZ007
    label = start.strftime("%a %d %b %Y, %H:%M")
    if event.end and "T" in event.end:
        label += datetime.strptime(event.end, "%Y-%m-%dT%H:%M").strftime("–%H:%M")  # noqa: DTZ007
    return label


def to_ics(events: list[Event], now: datetime | None = None) -> str:
    """iCalendar with floating (local) times: Calendar apps put them in the user's zone."""
    stamp = (now or datetime.now()).strftime("%Y%m%dT%H%M%S")  # noqa: DTZ005
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//tgclient//EN", "CALSCALE:GREGORIAN"]
    for event in events:
        uid = hashlib.sha1(f"{event.title}|{event.start}".encode()).hexdigest()[:16]
        lines += ["BEGIN:VEVENT", f"UID:{uid}@tgclient", f"DTSTAMP:{stamp}"]
        if event.all_day:
            day = datetime.strptime(event.start, "%Y-%m-%d")  # noqa: DTZ007
            lines.append(f"DTSTART;VALUE=DATE:{day:%Y%m%d}")
            lines.append(f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}")
        else:
            start = datetime.strptime(event.start, "%Y-%m-%dT%H:%M")  # noqa: DTZ007
            end = (datetime.strptime(event.end, "%Y-%m-%dT%H:%M")  # noqa: DTZ007
                   if event.end and "T" in event.end else start + timedelta(hours=1))
            lines += [f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{end:%Y%m%dT%H%M%S}"]
        lines.append(f"SUMMARY:{_ics_text(event.title)}")
        if event.location:
            lines.append(f"LOCATION:{_ics_text(event.location)}")
        if event.notes:
            lines.append(f"DESCRIPTION:{_ics_text(event.notes)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _ics_text(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\n", "\\n"))


# --- collecting answers in a group ----------------------------------------------------------

ANSWERS_PROMPT = f"""\
Someone asked a question (or made a call: a poll, an invitation, a request) in a Telegram
group. Find out how each participant answered.

Input: the question message, the messages that came after it, and the list of members.
Messages are formatted `[m<id>] <date time> <sender>: <text>`.

Output markdown:
1. One line restating the question.
2. A table `| {{H:person}} | {{H:answer}} | {{H:source}} |` (exactly this header) with one row
   per person who answered: the answer class first ({{H:classes}}), then a few words of what
   they said, and the cited message as [m<id>] in the last column. Group rows by answer class.
3. A line `**{{H:no_answer}}:** name, name, …` listing members who didn't respond (skip the
   asker).
- Messages unrelated to the question are not answers. {CITE_RULE}
- Write in {{language}} (the table too).
"""


def answers_prompt(chat_title: str, question_line: str, lines: str,
                   members: list[str], language: str = "en") -> list[dict[str, Any]]:
    roster = ", ".join(members) if members else "(unknown)"
    return system_user(_lang(ANSWERS_PROMPT, language), f"Chat: {chat_title}\nQuestion: {question_line}\n\n"
                                       f"Messages after it:\n{lines}\n\nMembers: {roster}")


# --- across chats: digest and promises ------------------------------------------------------

DIGEST_PROMPT = f"""\
You write a digest of several Telegram chats for their reader: what matters since they last
looked.

Input: chats as `## <chat title>` sections with messages formatted
`[m<ref>] <date time> <sender>: <text>`.

Rules:
- Markdown: a `##` heading per chat that has something worth knowing, most important chats
  first, 1–4 short bullets each. Skip chats with only small talk; end with one line listing
  them as "{{H:nothing}}: …".
- Put requests addressed to the reader, decisions, dates and deadlines first.
- {CITE_RULE}
- Write in {{language}}, whatever language the messages are in. No preamble.
"""

PROMISES_PROMPT = f"""\
You find what the reader promised to other people in Telegram chats: things they said they
would do, send, check, pay, attend — that others may be waiting for.

Input: chats as `## <chat title>` sections. Lines marked `(me)` are the reader's messages;
others give context. Messages are formatted `[m<ref>] <date time> <sender>: <text>`.

Rules:
- Markdown bullets: `**to whom** — what, by when (if said)` and the citation of the reader's
  message. Group by chat with `##` headings. Only real commitments by the reader, not ideas
  or other people's promises. If there are none, say so in one line.
- {CITE_RULE}
- Write in {{language}}, whatever language the messages are in. No preamble.
"""


@dataclass
class ChatBlock:
    chat_id: int
    title: str
    messages: list[Message]


def render_multi(
    blocks: list[ChatBlock],
    sender_name: Callable[[Message], str],
    transcript: Callable[[Message], str | None],
    mine: Callable[[Message], bool] = lambda m: False,
    max_chars: int = summary.MAX_CHARS,
) -> tuple[str, Source]:
    """Several chats -> `## title` sections with short refs [m1], [m2], ... linked to
    tgc://message/<chat>/<id>. Older messages are dropped first when it gets too long."""
    kept: list[tuple[ChatBlock, list[tuple[Message, str]]]] = []
    total = 0
    for block in blocks:
        chosen: list[tuple[Message, str]] = []
        for message in reversed(block.messages):  # newest first while budgeting
            line = summary.message_line(message, sender_name(message), transcript)
            if not line:
                continue
            if total + len(line) > max_chars:
                break
            total += len(line) + 8
            chosen.append((message, line))
        if chosen:
            chosen.reverse()
            kept.append((block, chosen))

    sections: list[list[str]] = []
    times: dict[int, str] = {}
    targets: dict[int, str] = {}
    ref = 0
    for block, chosen in kept:  # references numbered in reading order
        lines = [f"## {block.title}"]
        for message, line in chosen:
            ref += 1
            stamp = datetime.fromtimestamp(message.get("date", 0))  # noqa: DTZ006
            times[ref] = f"{stamp:%d.%m %H:%M}"
            targets[ref] = f"{block.chat_id}/{message['id']}"
            marker = " (me)" if mine(message) else ""
            lines.append(f"[m{ref}]{marker} {line}")
        sections.append(lines)
    text = "\n\n".join("\n".join(section) for section in sections)
    count = len(times)
    return text, Source(times=times, count=count, truncated=False, targets=targets)


def digest_prompt(reader: str, text: str, since: str,
                  language: str = "en") -> list[dict[str, Any]]:
    return system_user(_lang(DIGEST_PROMPT, language),
                       f"Reader: {reader or 'unknown'}\nSince: {since}\n\n{text}")


def promises_prompt(reader: str, text: str, language: str = "en") -> list[dict[str, Any]]:
    return system_user(_lang(PROMISES_PROMPT, language), f"Reader: {reader or 'unknown'}\n\n{text}")


# --- reply suggestions, relevance -----------------------------------------------------------

REPLY_PROMPT = """\
You draft the reader's next message in a Telegram chat. The reader will edit and send it
themselves.

Input: the recent messages formatted `<date time> <sender>: <text>`, oldest first, and
optionally the message to reply to.

Rules: write as the reader, in the language the chat uses with them, matching how the reader
usually writes there (length, emoji, formality), in a {tone} tone. Answer what is actually
asked; don't invent facts, dates or promises — leave a short placeholder like […] where the
reader must fill something in. Output only the message text.
"""


def reply_prompt(chat_title: str, reader: str, lines: str, tone: str,
                 reply_to: str = "") -> list[dict[str, Any]]:
    system = REPLY_PROMPT.format(tone=TONES.get(tone, TONES["neutral"]))
    target = f"\n\nReply to: {reply_to}" if reply_to else ""
    return system_user(system, f"Chat: {chat_title}\nReader: {reader or 'unknown'}\n\n"
                               f"{lines}{target}")


RELEVANCE_PROMPT = """\
Decide whether a new Telegram message deserves a notification for the reader.

Answer "yes" if it is addressed to the reader (by name, as a reply, or by context), asks them
something, needs their action or decision, or contains news, a date or a change that
concerns them. Answer "no" for small talk, reactions, jokes, and discussions that don't
involve them. Output only "yes" or "no".
"""


def relevance_prompt(reader: str, chat_title: str, context: str,
                     new_line: str) -> list[dict[str, Any]]:
    return system_user(RELEVANCE_PROMPT,
                       f"Reader: {reader or 'unknown'}\nChat: {chat_title}\n\n"
                       f"Earlier messages:\n{context or '(none)'}\n\nNew message:\n{new_line}")


def is_yes(reply: str) -> bool:
    return reply.strip().strip(".!\"'").lower().startswith(("yes", "да", "ano", "так"))


# --- documents ------------------------------------------------------------------------------

DOCUMENT_PROMPT = """\
You answer questions about a document the reader received in a Telegram chat. Use only the
document. Answer in {language}; quote short passages (in the original) or give page numbers
when it helps. If the document doesn't say, say so.
"""

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".html",
                   ".htm", ".log", ".ini", ".toml", ".py", ".js", ".ts", ".java", ".c", ".cpp",
                   ".h", ".go", ".rs", ".sh", ".sql", ".rtf", ".srt"}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 150_000


def document_kind(file_name: str, mime: str) -> str:
    """pdf | text | '' (unsupported)."""
    name = file_name.lower()
    if name.endswith(".pdf") or mime == "application/pdf":
        return "pdf"
    if mime.startswith("text/") or any(name.endswith(ext) for ext in TEXT_EXTENSIONS):
        return "text"
    return ""


def document_messages(question: str, file_name: str, kind: str,
                      data: bytes, language: str = "en") -> list[dict[str, Any]]:
    import base64

    if kind == "pdf":
        part: dict[str, Any] = {"type": "file", "file": {
            "filename": file_name or "document.pdf",
            "file_data": "data:application/pdf;base64," + base64.b64encode(data).decode("ascii"),
        }}
    else:
        text = data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]
        part = {"type": "text", "text": f"Document {file_name}:\n\n{text}"}
    return [
        {"role": "system", "content": _lang(DOCUMENT_PROMPT, language)},
        {"role": "user", "content": [part, {"type": "text", "text": f"Question: {question}"}]},
    ]


# PDFs are read by the model itself (no third-party parsing service in between).
PDF_PLUGINS = [{"id": "file-parser", "pdf": {"engine": "native"}}]


def cache_key(model: str, messages: list[dict[str, Any]], extra: str = "") -> str:
    blob = json.dumps([model, messages, extra], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


# --- one message: explain it, suggest replies ------------------------------------------------

NO_GUESSING = """\
- Use only what the messages say. Never invent motives, subtext or feelings; if the context is
  too thin to tell, say plainly that there isn't enough context.
- Do not infer sensitive traits of anyone (health, religion, ethnicity, sexuality, political
  views, finances)."""

EXPLAIN_PROMPT = f"""\
You help the reader understand one message in a Telegram chat (marked >>> in the input).

Input: optionally an earlier summary of the chat, the reply chain the message belongs to, and
messages around it (before and after), formatted `[m<id>] <date time> <sender>: <text>`.

Output markdown in {{language}} with these `##` sections in this order. Write each heading
exactly as given below, nothing after it on that line (the part in parentheses tells you what
goes into the section, never copy it). Leave out any section you have nothing real for:
## {{H:gist}}
  (one or two sentences: what the message says; most useful for long or messy ones)
## {{H:want}}
  (a question, request, decision or deadline aimed at the reader)
## {{H:context}}
  (what it refers to in the conversation; cite every statement as [m<id>])
## {{H:tone}}
  (only if it stands out: urgency, irritation, irony; one line)
## {{H:unclear}}
  (what is worth clarifying before answering)

Rules:
{NO_GUESSING}
- {CITE_RULE}
- Short. No preamble, no closing remarks. Translate the gist if the message is in another
  language.
"""

REPLY_OPTIONS_PROMPT = f"""\
You draft replies the reader could send to one message in a Telegram chat (marked >>>).

Input: optionally an earlier summary, the reply chain, messages around it, and examples of how
the reader writes in this chat.

Output exactly this plain-text format, nothing else:
ANALYSIS: <one or two short lines in {{language}}: what is asked of the reader>
### <strategy label in {{language}}, e.g. agree / ask to clarify / decline politely / postpone>
<the reply, ready to send>
TRANSLATION: <the reply translated into {{language}}; omit this line if the reply is already in
{{language}}>
(repeat ### blocks: {{count}} options, each a genuinely different strategy, not rewordings)

Rules for the replies:
- Write in the language this chat uses with the reader (not necessarily {{language}}), as the
  reader: match their length, emoji and formality from the examples. {{modifier}}
- Answer what is actually asked. Don't invent facts, dates, prices or promises: put a short
  placeholder like […] where the reader must fill something in.
{NO_GUESSING}
"""

MODIFIERS = {
    "": "",
    "shorter": "Make every reply noticeably shorter than usual for this chat.",
    "formal": "Use a more formal, polite register than usual for this chat.",
    "another": "Suggest strategies different from the ones listed under 'Already suggested'.",
}


@dataclass
class MessageContext:
    """What goes to the model for one message: built once, reused by Explain and Suggest."""
    text: str  # the prompt body
    source: Source  # for links
    count: int  # messages included (shown in the menu before anything is sent)
    target_line: str
    language_hint: str = ""


def message_context(
    chat_title: str, reader: str, target: Message, chain: list[Message],
    around: list[Message], sender_name: Callable[[Message], str],
    transcript: Callable[[Message], str | None], chat_summary: str = "",
    my_examples: list[str] | None = None,
) -> MessageContext:
    """Reply chain (oldest first) + messages around (oldest first, the target marked >>>)."""
    seen: set[int] = set()
    times: dict[int, str] = {}
    lines_chain: list[str] = []
    lines_around: list[str] = []
    target_line = ""
    for group, out in ((chain, lines_chain), (around, lines_around)):
        for message in sorted(group, key=lambda m: m["id"]):
            if message["id"] in seen:
                continue  # an ancestor in the chain that is also nearby: shown once
            line = summary.message_line(message, sender_name(message), transcript)
            if not line:
                continue
            seen.add(message["id"])
            stamp = datetime.fromtimestamp(message.get("date", 0))  # noqa: DTZ006
            times[message["id"]] = f"{stamp:%d.%m %H:%M}"
            marker = ">>> " if message["id"] == target["id"] else ""
            out.append(f"{marker}[m{message['id']}] {line}")
            if marker:
                target_line = out[-1]
    if not target_line:  # the target had no text (e.g. a sticker): still name it
        target_line = f">>> [m{target['id']}] " + (
            summary.message_line(target, sender_name(target), transcript) or "(no text)")
        lines_around.append(target_line)
        times[target["id"]] = ""
    parts = [f"Chat: {chat_title}", f"Reader: {reader or 'unknown'}"]
    if chat_summary:
        parts.append("Earlier summary of this chat (may be outdated):\n"
                     + re.sub(r"\[([^\]]+)\]\(tgc://[^)]+\)", r"\1", chat_summary)[:3000])
    if len(lines_chain) > 1:
        parts.append("Reply chain, oldest first:\n" + "\n".join(lines_chain))
    parts.append("Messages around it, oldest first:\n" + "\n".join(lines_around))
    if my_examples:
        parts.append("How the reader writes in this chat:\n"
                     + "\n".join(f"- {e}" for e in my_examples))
    parts.append(f"Message to look at: {target_line}")
    count = len(seen | {target["id"]})
    return MessageContext("\n\n".join(parts), Source(times=times, count=count, truncated=False),
                          count, target_line)


def explain_prompt(context: MessageContext, language: str) -> list[dict[str, Any]]:
    return system_user(_lang(EXPLAIN_PROMPT, language), context.text)


def reply_options_prompt(context: MessageContext, language: str, modifier: str = "",
                         already: list[str] | None = None, count: int = 3,
                         ) -> list[dict[str, Any]]:
    system = (_lang(REPLY_OPTIONS_PROMPT, language)
              .replace("{count}", str(count))
              .replace("{modifier}", MODIFIERS.get(modifier, "")))
    body = context.text
    if already:
        body += "\n\nAlready suggested:\n" + "\n".join(f"- {a}" for a in already)
    return system_user(system, body)


def parse_reply_options(text: str) -> tuple[str, list[dict[str, str]]]:
    """(analysis, [{label, text, translation}]) from the REPLY_OPTIONS_PROMPT format;
    tolerates a half-streamed answer."""
    analysis = ""
    options: list[dict[str, str]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.upper().startswith("ANALYSIS:") and current is None:
            analysis = line.split(":", 1)[1].strip()
        elif line.startswith("###"):
            current = {"label": line.lstrip("#").strip(), "lines": [], "translation": ""}
            options.append(current)  # type: ignore[arg-type]
        elif current is not None and line.upper().startswith("TRANSLATION:"):
            current["translation"] = line.split(":", 1)[1].strip()
        elif current is not None and not current["translation"]:
            current["lines"].append(line)
        elif current is None and analysis and line.strip():
            analysis += " " + line.strip()
    result = []
    for option in options:
        body = "\n".join(option["lines"]).strip()  # type: ignore[index]
        if body:
            result.append({"label": option["label"], "text": body,  # type: ignore[index]
                           "translation": option["translation"]})  # type: ignore[index]
    return analysis, result


def reply_markdown(text: str) -> str:
    """The streamed REPLY_OPTIONS format as readable markdown for the panel."""
    out = []
    for line in text.splitlines():
        if line.upper().startswith("ANALYSIS:"):
            out.append("*" + line.split(":", 1)[1].strip() + "*")
        elif line.upper().startswith("TRANSLATION:"):
            out.append("_" + line.split(":", 1)[1].strip() + "_")
        else:
            out.append(line)
    return "\n".join(out)
