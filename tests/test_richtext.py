from __future__ import annotations

import unittest

from tgclient.store.richtext import Palette, formatted_to_html, utf16_index_map

P = Palette(link="#123456", code_background="#eee", spoiler="#999", mono="Mono")


def entity(kind: str, offset: int, length: int, **extra: object) -> dict:
    return {"offset": offset, "length": length, "type": {"@type": kind, **extra}}


def body(html: str) -> str:
    prefix = '<div style="white-space:pre-wrap">'
    assert html.startswith(prefix) and html.endswith("</div>"), html
    return html[len(prefix):-len("</div>")]


class RichTextTest(unittest.TestCase):
    def test_utf16_offsets_with_emoji(self) -> None:
        text = "🚀 go"  # rocket is 2 UTF-16 units
        self.assertEqual(utf16_index_map(text), [0, 0, 1, 2, 3, 4])
        ft = {"text": text, "entities": [entity("textEntityTypeBold", 3, 2)]}
        self.assertEqual(body(formatted_to_html(ft, P)), "🚀 <b>go</b>")

    def test_escaping_and_newlines(self) -> None:
        ft = {"text": "a < b && c\nnext", "entities": []}
        self.assertEqual(body(formatted_to_html(ft, P)), "a &lt; b &amp;&amp; c\nnext")

    def test_nested_and_links(self) -> None:
        ft = {"text": "see example.com now", "entities": [
            entity("textEntityTypeItalic", 0, 15),
            entity("textEntityTypeUrl", 4, 11),
        ]}
        self.assertEqual(
            body(formatted_to_html(ft, P)),
            '<i>see <a href="https://example.com" style="text-decoration:none">'
            '<span style="color:#123456">example.com'
            '</span></a></i> now',
        )

    def test_text_url_attribute_is_escaped(self) -> None:
        ft = {"text": "x", "entities": [entity("textEntityTypeTextUrl", 0, 1, url='a"b')]}
        self.assertIn('href="a&quot;b"', formatted_to_html(ft, P))

    def test_partial_overlap_is_clipped(self) -> None:
        ft = {"text": "abcdef", "entities": [
            entity("textEntityTypeBold", 0, 4),
            entity("textEntityTypeItalic", 2, 4),
        ]}
        self.assertEqual(body(formatted_to_html(ft, P)), "<b>ab<i>cd</i></b>ef")

    def test_code_spoiler_mention(self) -> None:
        ft = {"text": "`x` @bob secret", "entities": [
            entity("textEntityTypeCode", 0, 3),
            entity("textEntityTypeMention", 4, 4),
            entity("textEntityTypeSpoiler", 9, 6),
        ]}
        out = formatted_to_html(ft, P)
        self.assertIn("font-family:'Mono';background-color:#eee", out)
        self.assertIn('href="tg://resolve?domain=bob"', out)
        self.assertIn("background-color:#999;color:#999", out)

    def test_out_of_range_entities_are_ignored(self) -> None:
        ft = {"text": "hi", "entities": [entity("textEntityTypeBold", 5, 3),
                                          entity("textEntityTypeBold", 0, 0)]}
        self.assertEqual(body(formatted_to_html(ft, P)), "hi")

    def test_tail_and_empty(self) -> None:
        self.assertEqual(formatted_to_html({"text": ""}, P), "")
        self.assertTrue(formatted_to_html({"text": "a"}, P, tail="<span>t</span>")
                        .endswith("a<span>t</span></div>"))


if __name__ == "__main__":
    unittest.main()


class SummaryMarkdownTest(unittest.TestCase):
    def test_lists_inline_and_links(self) -> None:
        from tgclient.store.markdown import markdown_to_html

        palette = Palette(link="#123456")
        out = markdown_to_html(
            "## Plans\n- **Friday** at `5pm` [10:02](tgc://message/3)\n  - *maybe* <b>\n"
            "1. first\n\nplain [x](javascript:alert) text", palette)
        self.assertIn("<b>Plans</b>", out)
        self.assertIn("•&nbsp;&nbsp;<b>Friday</b>", out)
        self.assertIn('href="tgc://message/3"', out)
        self.assertIn("color:#123456", out)
        self.assertIn("margin-left:32px", out)  # nested bullet
        self.assertIn("<i>maybe</i> &lt;b&gt;", out)
        self.assertIn("1.&nbsp;&nbsp;first", out)
        self.assertNotIn("javascript", out)
        self.assertIn("plain x text", out)
        self.assertIn("background-color", out)
