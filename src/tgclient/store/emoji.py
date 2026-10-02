"""Emoji catalog for the picker: Unicode groups (emoji.json), name search, recently used.

Qt-free. The data comes from scripts/gen_emoji.py (Emoji 15.0, base emoji without skin tones).
Recently used emoji are kept in a small JSON file (or only in memory when no path is given).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DATA = Path(__file__).with_name("emoji.json")
RECENT = "recent"
MAX_RECENT = 40
_WORD = re.compile(r"[\w']+", re.UNICODE)

# Tab glyph and short key for each Unicode group, in Unicode's order.
_GROUPS = {
    "Smileys & Emotion": ("smileys", "\U0001F600"),
    "People & Body": ("people", "\U0001F44B"),
    "Animals & Nature": ("nature", "\U0001F43B"),
    "Food & Drink": ("food", "\U0001F354"),
    "Travel & Places": ("travel", "\U0001F697"),
    "Activities": ("activities", "⚽"),
    "Objects": ("objects", "\U0001F4A1"),
    "Symbols": ("symbols", "❤️"),
    "Flags": ("flags", "\U0001F3C1"),
}

Emoji = tuple[str, str]  # (emoji, name)


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    glyph: str  # "" for the recent tab (drawn with an icon)


class EmojiCatalog:
    def __init__(self, recent_path: Path | None = None) -> None:
        data = json.loads(DATA.read_text(encoding="utf-8"))
        self._groups: dict[str, list[Emoji]] = {}
        self.categories: list[Category] = [Category(RECENT, "Recently used", "")]
        for title, items in data["groups"]:
            key, glyph = _GROUPS.get(title, (title.lower(), items[0][0]))
            self._groups[key] = [(e, n) for e, n in items]
            self.categories.append(Category(key, title, glyph))
        self._names = {e: n for items in self._groups.values() for e, n in items}
        self._recent_path = recent_path
        self.recent: list[str] = self._load_recent()

    def items(self, category: str) -> list[Emoji]:
        if category == RECENT:
            return [(e, self._names.get(e, "")) for e in self.recent]
        return self._groups.get(category, [])

    def search(self, query: str, limit: int = 200) -> list[Emoji]:
        """Every query word must start a word of the emoji's name ("hea" finds "red heart")."""
        words = _WORD.findall(query.lower())
        if not words:
            return []
        found = []
        for items in self._groups.values():
            for emoji, name in items:
                name_words = _WORD.findall(name.lower())
                if all(any(n.startswith(w) for n in name_words) for w in words):
                    found.append((emoji, name))
                    if len(found) >= limit:
                        return found
        return found

    def use(self, emoji: str) -> None:
        self.recent = [emoji] + [e for e in self.recent if e != emoji][: MAX_RECENT - 1]
        if self._recent_path is not None:
            try:
                self._recent_path.parent.mkdir(parents=True, exist_ok=True)
                self._recent_path.write_text(json.dumps(self.recent, ensure_ascii=False))
            except OSError as e:
                log.warning("Could not save recent emoji: %s", e)

    def _load_recent(self) -> list[str]:
        if self._recent_path is None or not self._recent_path.exists():
            return []
        try:
            recent = json.loads(self._recent_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [e for e in recent if isinstance(e, str)][:MAX_RECENT]
