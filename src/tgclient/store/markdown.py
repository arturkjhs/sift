"""The small markdown subset LLM summaries use, rendered to Qt rich text HTML.

Qt's own MarkdownText ignores the theme's link color and indents lists by 40px, so summaries are
converted here: headings, bullet and numbered lists (nested by indentation), paragraphs, simple
`| a | b |` tables, and inline **bold**, *italic*, `code` and [links](url). Anything else stays plain text.
Only http(s) and tgc:// links become clickable.
"""

from __future__ import annotations

import html
import re

from .richtext import Palette

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$")
_INLINE = re.compile(
    r"\[(?P<label>[^\]\n]+)\]\((?P<url>[^)\s]+)\)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|`(?P<code>[^`\n]+)`"
    r"|(?<![\w*])[*_](?P<italic>[^\s*_](?:.*?[^\s*_])?)[*_](?![\w*])"
)
_SAFE_SCHEMES = ("https://", "http://", "tgc://")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


def markdown_to_html(text: str, palette: Palette) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []
    table: list[list[str]] = []

    def flush() -> None:
        if paragraph:
            blocks.append(_block(" ".join(paragraph), palette, top=6))
            paragraph.clear()
        if table:
            blocks.append(_table(table, palette))
            table.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("|"):
            if paragraph:
                blocks.append(_block(" ".join(paragraph), palette, top=6))
                paragraph.clear()
            if not _TABLE_RULE.match(line.strip()):
                table.append([cell.strip() for cell in line.strip().strip("|").split("|")])
            continue
        if table:
            flush()
        if not line.strip():
            flush()
            continue
        if heading := _HEADING.match(line.strip()):
            flush()
            blocks.append(_block(f"**{heading.group(2)}**", palette, top=10))
            continue
        if bullet := _BULLET.match(line):
            flush()
            level = min(len(bullet.group(1).replace("\t", "  ")) // 2, 3)
            marker = bullet.group(2)
            glyph = "•" if marker in "-*+" else html.escape(marker)
            blocks.append(_block(bullet.group(3), palette, top=3, level=level + 1, glyph=glyph))
            continue
        paragraph.append(line.strip())
    flush()
    return "".join(blocks)


def _table(rows: list[list[str]], palette: Palette) -> str:
    """First row is the header. Qt rich text tables: borders via the table attributes."""
    out = [(f'<table border="1" cellspacing="0" cellpadding="4" width="100%" '
            f'style="margin-top:6px; border-color:{palette.code_background}; '
            f'border-style:solid">')]
    width = max(len(row) for row in rows)
    for index, row in enumerate(rows):
        cells = [*row, *[""] * (width - len(row))]
        tag = "th" if index == 0 else "td"
        out.append("<tr>" + "".join(
            f'<{tag} align="left">{_inline(cell, palette)}</{tag}>' for cell in cells) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _block(text: str, palette: Palette, top: int, level: int = 0, glyph: str = "") -> str:
    style = f"margin-top:{top}px; margin-bottom:0px"
    body = _inline(text, palette)
    if level:
        # Hanging indent: the glyph sits left of the wrapped text.
        style += f"; margin-left:{4 + 14 * level}px; text-indent:-12px"
        body = f"{glyph}&nbsp;&nbsp;{body}"
    return f'<p style="{style}">{body}</p>'


def _inline(text: str, palette: Palette) -> str:
    out: list[str] = []
    position = 0
    for match in _INLINE.finditer(text):
        out.append(html.escape(text[position:match.start()], quote=False))
        position = match.end()
        if (label := match.group("label")) is not None:
            url = match.group("url")
            inner = _inline(label, palette)
            if url.startswith(_SAFE_SCHEMES):
                out.append(
                    f'<a href="{html.escape(url, quote=True)}" style="text-decoration:none">'
                    f'<span style="color:{palette.link}">{inner}</span></a>')
            else:
                out.append(inner)
        elif (bold := match.group("bold")) is not None:
            out.append(f"<b>{_inline(bold, palette)}</b>")
        elif (code := match.group("code")) is not None:
            out.append(
                f'<span style="font-family:\'{palette.mono}\'; '
                f'background-color:{palette.code_background}">'
                f"{html.escape(code, quote=False)}</span>")
        else:
            out.append(f"<i>{_inline(match.group('italic'), palette)}</i>")
    out.append(html.escape(text[position:], quote=False))
    return "".join(out)
