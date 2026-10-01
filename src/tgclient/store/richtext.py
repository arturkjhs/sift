"""Convert TDLib formattedText to the HTML subset Qt's rich text engine understands.

TDLib entity offsets/lengths are in UTF-16 code units, Python indexes code points:
emoji outside the BMP take 2 units, so offsets must be remapped.
Entities are nested or disjoint by Telegram's rules; a partial overlap (shouldn't happen) is
clipped to the enclosing entity instead of producing broken markup.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any


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
        case "textEntityTypeSpoiler":
            return (
                f'<span style="background-color:{palette.spoiler};color:{palette.spoiler}">',
                "</span>",
            )
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


def formatted_to_html(formatted: dict[str, Any] | None, palette: Palette, tail: str = "") -> str:
    """Return HTML for a formattedText. `tail` is raw HTML appended at the end (time spacer)."""
    text = (formatted or {}).get("text", "")
    if not text and not tail:
        return ""
    index = utf16_index_map(text)
    limit = len(index) - 1

    spans: list[_Span] = []
    for entity in (formatted or {}).get("entities", []):
        offset = entity.get("offset", 0)
        length = entity.get("length", 0)
        if length <= 0 or offset < 0 or offset >= limit:
            continue
        start, end = index[offset], index[min(offset + length, limit)]
        if start >= end:
            continue
        tags = _tags(entity.get("type", {}), text[start:end], palette)
        if tags:
            spans.append(_Span(start, end, *tags))
    spans.sort(key=lambda s: (s.start, -s.end))

    boundaries = sorted({0, len(text), *(s.start for s in spans), *(s.end for s in spans)})
    out: list[str] = []
    stack: list[_Span] = []
    position = 0
    next_span = 0
    for boundary in boundaries:
        out.append(html.escape(text[position:boundary], quote=False))
        position = boundary
        while stack and stack[-1].end <= boundary:
            out.append(stack.pop().close)
        while next_span < len(spans) and spans[next_span].start == boundary:
            span = spans[next_span]
            next_span += 1
            if stack and span.end > stack[-1].end:
                span.end = stack[-1].end  # clip a partial overlap
            out.append(span.open)
            stack.append(span)
    while stack:
        out.append(stack.pop().close)

    return f'<div style="white-space:pre-wrap">{"".join(out)}{tail}</div>'
