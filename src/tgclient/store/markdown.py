"""The small markdown subset LLM summaries use, rendered to Qt rich text HTML.

Qt's own MarkdownText ignores the theme's link color and indents lists by 40px, so summaries are
converted here: headings, bullet and numbered lists (nested by indentation), paragraphs, and
inline **bold**, *italic*, `code` and [links](url). Anything else stays plain text.
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


def markdown_to_html(text: str, palette: Palette) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(_block(" ".join(paragraph), palette, top=6))
            paragraph.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
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
