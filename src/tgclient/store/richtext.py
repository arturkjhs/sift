"""Convert TDLib formattedText to the HTML subset Qt's rich text engine understands.

TDLib entity offsets/lengths are in UTF-16 code units, Python indexes code points:
emoji outside the BMP take 2 units, so offsets must be remapped.
Entities are nested or disjoint by Telegram's rules; a partial overlap (shouldn't happen) is
clipped to the enclosing entity instead of producing broken markup.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Custom emoji id -> image URL, or None while unknown (the fallback emoji text is shown then).
CustomEmoji = Callable[[str], str | None]
EMOJI_SIZE = 20


@dataclass(frozen=True)
class Palette:
    link: str = "#0E7C66"
    code_background: str = "#E8ECF0"
    spoiler: str = "#9AA3AE"
    mono: str = "monospace"


@dataclass
class _Span:
    start: int
    end: int
    open: str
    close: str
    replace: bool = False  # the span's text is replaced by `open` (custom emoji image)


def utf16_index_map(text: str) -> list[int]:
    """index_map[utf16_offset] -> Python string index (len == utf16 length + 1)."""
    result: list[int] = []
    for i, ch in enumerate(text):
        result.append(i)
        if ord(ch) > 0xFFFF:
            result.append(i)  # second half of a surrogate pair maps to the same character
    result.append(len(text))
    return result


def _attr(value: str) -> str:
    return html.escape(value, quote=True)


def _link(href: str, palette: Palette) -> tuple[str, str]:
    opening = (
        f'<a href="{_attr(href)}" style="text-decoration:none">'
        f'<span style="color:{palette.link}">'
    )
    return opening, "</span></a>"


def _tags(entity_type: dict[str, Any], segment: str, palette: Palette) -> tuple[str, str] | None:
    match entity_type.get("@type"):
        case "textEntityTypeBold":
            return "<b>", "</b>"
        case "textEntityTypeItalic":
            return "<i>", "</i>"
        case "textEntityTypeUnderline":
            return "<u>", "</u>"
        case "textEntityTypeStrikethrough":
            return "<s>", "</s>"
        case "textEntityTypeCode" | "textEntityTypePre" | "textEntityTypePreCode":
            # TODO: render Pre as a separate block with its own background
            mono = f"font-family:'{palette.mono}';background-color:{palette.code_background}"
            return f'<span style="{mono}">', "</span>"
        case "textEntityTypeBlockQuote" | "textEntityTypeExpandableBlockQuote":
            return "<i>", "</i>"  # TODO: proper quote block with a side bar
        case "textEntityTypeTextUrl":
            return _link(entity_type.get("url", ""), palette)
        case "textEntityTypeUrl":
            href = segment if "://" in segment else f"https://{segment}"
            return _link(href, palette)
        case "textEntityTypeEmailAddress":
            return _link(f"mailto:{segment}", palette)
        case "textEntityTypePhoneNumber":
            return _link(f"tel:{segment}", palette)
        case "textEntityTypeMention":
            return _link(f"tg://resolve?domain={segment.lstrip('@')}", palette)
        case "textEntityTypeMentionName":
            return _link(f"tg://user?id={entity_type.get('user_id', 0)}", palette)
        case "textEntityTypeHashtag" | "textEntityTypeCashtag" | "textEntityTypeBotCommand":
            return _link(f"tg://search?q={segment}", palette)
    return None  # custom emoji etc.: plain text (the fallback emoji is in the text)


def _spoiler(palette: Palette, link: str) -> tuple[str, str]:
    """Hidden text: painted over with the spoiler color; a link (tgc://spoiler/<id>) reveals
    it on click. Without a link nothing would ever show it."""
    hidden = f'<span style="background-color:{palette.spoiler};color:{palette.spoiler}">'
    if not link:
        return hidden, "</span>"
    return (f'<a href="{_attr(link)}" style="text-decoration:none">{hidden}',
            "</span></a>")


def formatted_to_html(formatted: dict[str, Any] | None, palette: Palette, tail: str = "",
                      custom_emoji: CustomEmoji | None = None, spoiler_link: str = "",
                      reveal_spoilers: bool = False) -> str:
    """Return HTML for a formattedText. `tail` is raw HTML appended at the end (time spacer).
    `custom_emoji` turns custom emoji entities into inline images when their URL is known.
    Spoilers stay hidden (clickable with `spoiler_link`) unless `reveal_spoilers`."""
    text = (formatted or {}).get("text", "")
    if not text and not tail:
        return ""
    index = utf16_index_map(text)
    limit = len(index) - 1

    entities = (formatted or {}).get("entities", [])
    hidden: list[tuple[int, int]] = []  # spoiler ranges in UTF-16 units
    if not reveal_spoilers:
        hidden = [(e.get("offset", 0), e.get("offset", 0) + e.get("length", 0))
                  for e in entities if e.get("type", {}).get("@type") == "textEntityTypeSpoiler"]

    spans: list[_Span] = []
    for entity in entities:
        offset = entity.get("offset", 0)
        length = entity.get("length", 0)
        if length <= 0 or offset < 0 or offset >= limit:
            continue
        start, end = index[offset], index[min(offset + length, limit)]
        if start >= end:
            continue
        entity_type = entity.get("type", {})
        if entity_type.get("@type") == "textEntityTypeSpoiler":
            if not reveal_spoilers:
                spans.append(_Span(start, end, *_spoiler(palette, spoiler_link)))
            continue
        if any(a < offset + length and offset < b for a, b in hidden):
            continue  # a link color or an emoji image inside a spoiler would give it away
        if entity_type.get("@type") == "textEntityTypeCustomEmoji" and custom_emoji:
            url = custom_emoji(str(entity_type.get("custom_emoji_id", "")))
            if url:
                image = (f'<img src="{_attr(url)}" width="{EMOJI_SIZE}" height="{EMOJI_SIZE}" '
                         f'style="vertical-align:middle">')
                spans.append(_Span(start, end, image, "", replace=True))
            continue
        tags = _tags(entity_type, text[start:end], palette)
        if tags:
            spans.append(_Span(start, end, *tags))
    spans.sort(key=lambda s: (s.start, -s.end))

    boundaries = sorted({0, len(text), *(s.start for s in spans), *(s.end for s in spans)})
    out: list[str] = []
    stack: list[_Span] = []
    position = 0
    next_span = 0
    replaced = 0  # inside a replaced span: its text isn't output
    for boundary in boundaries:
        if not replaced:
            out.append(html.escape(text[position:boundary], quote=False))
        position = boundary
        while stack and stack[-1].end <= boundary:
            closed = stack.pop()
            replaced -= closed.replace
            out.append(closed.close)
        while next_span < len(spans) and spans[next_span].start == boundary:
            span = spans[next_span]
            next_span += 1
            if stack and span.end > stack[-1].end:
                span.end = stack[-1].end  # clip a partial overlap
            if not replaced:
                out.append(span.open)
            replaced += span.replace
            stack.append(span)
    while stack:
        out.append(stack.pop().close)

    return f'<div style="white-space:pre-wrap">{"".join(out)}{tail}</div>'


_TAG = re.compile(r"(<[^>]*>)")


def highlight_html(html_text: str, query: str, background: str) -> str:
    """Mark the query's words (2+ characters, any case) in rich text, outside of tags."""
    words = sorted({html.escape(w) for w in query.split() if len(w) >= 2}, key=len,
                   reverse=True)
    if not words or not html_text:
        return html_text
    pattern = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)
    parts = _TAG.split(html_text)
    for index in range(0, len(parts), 2):  # text between tags
        parts[index] = pattern.sub(
            lambda m: f'<span style="background-color:{background}">{m.group(0)}</span>',
            parts[index])
    return "".join(parts)


_USER_LINK = re.compile(r"^tg://user\?id=(\d+)$")


def mention_names(formatted: dict[str, Any]) -> dict[str, Any]:
    """Links to tg://user?id=N (how the composer writes a mention of someone without a
    username) become textEntityTypeMentionName, which notifies them."""
    entities = []
    for entity in formatted.get("entities") or []:
        kind = entity.get("type") or {}
        match = _USER_LINK.match(kind.get("url", "")) if kind.get(
            "@type") == "textEntityTypeTextUrl" else None
        if match:
            entity = {**entity, "type": {"@type": "textEntityTypeMentionName",
                                         "user_id": int(match.group(1))}}
        entities.append(entity)
    return {**formatted, "entities": entities}

