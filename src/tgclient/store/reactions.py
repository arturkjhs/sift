"""Message reactions: TDLib messageReactions <-> simple keys usable from QML. Qt-free.

A reaction key is the emoji exactly as TDLib spells it, "custom:<id>" for custom emoji or "paid"
for star reactions. `display()` is what to draw: some emoji need U+FE0F to render in color.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Telegram's standard emoji reactions as TDLib spells them (the heart is "❤" without
# U+FE0F: another spelling is rejected). Used when TDLib can't tell what a chat allows.
DEFAULT_REACTIONS = [
    "\U0001F44D", "\U0001F44E", "❤", "\U0001F525", "\U0001F970", "\U0001F44F",
    "\U0001F601", "\U0001F914", "\U0001F92F", "\U0001F631", "\U0001F92C", "\U0001F622",
    "\U0001F389", "\U0001F929", "\U0001F92E", "\U0001F4A9", "\U0001F64F", "\U0001F44C",
    "\U0001F54A", "\U0001F921", "\U0001F971", "\U0001F974", "\U0001F60D", "\U0001F433",
    "❤‍\U0001F525", "\U0001F31A", "\U0001F32D", "\U0001F4AF", "\U0001F923",
    "⚡", "\U0001F34C", "\U0001F3C6", "\U0001F494", "\U0001F928", "\U0001F610",
    "\U0001F353", "\U0001F37E", "\U0001F48B", "\U0001F595", "\U0001F608", "\U0001F634",
    "\U0001F62D", "\U0001F913", "\U0001F47B", "\U0001F468‍\U0001F4BB", "\U0001F440",
    "\U0001F383", "\U0001F648", "\U0001F607", "\U0001F628", "\U0001F91D", "✍",
    "\U0001F917", "\U0001FAE1", "\U0001F385", "\U0001F384", "☃", "\U0001F485",
    "\U0001F92A", "\U0001F5FF", "\U0001F192", "\U0001F498", "\U0001F649", "\U0001F984",
    "\U0001F618", "\U0001F48A", "\U0001F64A", "\U0001F60E", "\U0001F47E",
    "\U0001F937‍♂", "\U0001F937", "\U0001F937‍♀", "\U0001F621",
]
QUICK = 7  # reactions in the context menu's row; the rest open from its expand button


def display(key: str) -> str:
    """How to draw a reaction. Symbols that default to text presentation (the heart, the
    snowman, ⚡, …) get U+FE0F, otherwise they render as small black glyphs."""
    if key == "paid":
        return "⭐"
    if key.startswith("custom:"):
        return "✦"  # until the custom emoji's picture is known
    if "️" in key:
        return key
    return "".join(ch + "️" if 0x2000 <= ord(ch) < 0x3300 and ch != "‍" else ch
                   for ch in key)


@dataclass(frozen=True)
class Reaction:
    key: str
    count: int
    chosen: bool
    recent: tuple[dict[str, Any], ...] = field(default=(), compare=False)  # MessageSenders

    @property
    def label(self) -> str:
        return display(self.key)


def reaction_key(reaction_type: dict[str, Any] | None) -> str:
    match (reaction_type or {}).get("@type"):
        case "reactionTypeEmoji":
            return reaction_type.get("emoji", "")  # type: ignore[union-attr]
        case "reactionTypeCustomEmoji":
            return f"custom:{reaction_type.get('custom_emoji_id', '')}"  # type: ignore[union-attr]
        case "reactionTypePaid":
            return "paid"
    return ""


def reaction_type(key: str) -> dict[str, Any]:
    if key == "paid":
        return {"@type": "reactionTypePaid"}
    if key.startswith("custom:"):
        return {"@type": "reactionTypeCustomEmoji", "custom_emoji_id": key.split(":", 1)[1]}
    return {"@type": "reactionTypeEmoji", "emoji": key}


def message_reactions(message: dict[str, Any]) -> list[Reaction]:
    info = message.get("interaction_info") or {}
    reactions = info.get("reactions") or {}
    if isinstance(reactions, dict):  # messageReactions (current TDLib)
        if reactions.get("are_tags"):
            return []  # Saved Messages tags, not reactions
        reactions = reactions.get("reactions") or []
    result = []
    for raw in reactions:
        key = reaction_key(raw.get("type"))
        if key and raw.get("total_count", 0) > 0:
            result.append(Reaction(key, raw["total_count"], bool(raw.get("is_chosen")),
                                   tuple(raw.get("recent_sender_ids") or ())))
    return result


def can_list_reactors(message: dict[str, Any]) -> bool:
    """Whether getMessageAddedReactions works for the message (small groups, own messages)."""
    reactions = (message.get("interaction_info") or {}).get("reactions")
    return isinstance(reactions, dict) and bool(reactions.get("can_get_added_reactions"))


def reactors_text(names: list[str], total: int) -> str:
    """'Olena, Petr and 3 more' for a reaction's tooltip; '' if nobody is known."""
    names = [n for n in names if n]
    if not names:
        return ""
    shown = ", ".join(names[:10])
    rest = total - min(len(names), 10)
    return f"{shown} and {rest} more" if rest > 0 else shown


def available_keys(available: dict[str, Any], limit: int | None = None) -> list[str]:
    """Emoji reactions the user may set, from getMessageAvailableReactions: the most used first
    (top, recent), then everything else the chat allows (popular). Custom emoji, paid and
    Premium-only reactions are left out."""
    keys: list[str] = []
    for group in ("top_reactions", "recent_reactions", "popular_reactions"):
        for item in available.get(group) or []:
            key = reaction_key(item.get("type"))
            if (key and not key.startswith("custom:") and key != "paid"
                    and not item.get("needs_premium") and key not in keys):
                keys.append(key)
    return keys[:limit] if limit else keys


def as_items(keys: list[str]) -> list[dict[str, str]]:
    """For QML: [{key, label}]."""
    return [{"key": key, "label": display(key)} for key in keys]
