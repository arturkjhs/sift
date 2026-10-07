"""Bot keyboards: buttons under a message (inline) and the keyboard a bot shows instead of
the system one (reply). Qt-free."""

from __future__ import annotations

from typing import Any

# Inline buttons the client acts on; the others show their text but say they aren't supported.
SUPPORTED = {"url", "callback", "copy", "user", "login"}
_KINDS = {
    "inlineKeyboardButtonTypeUrl": "url", "inlineKeyboardButtonTypeLoginUrl": "login",
    "inlineKeyboardButtonTypeCallback": "callback",
    "inlineKeyboardButtonTypeCallbackWithPassword": "password",
    "inlineKeyboardButtonTypeCallbackGame": "game",
    "inlineKeyboardButtonTypeSwitchInline": "switch", "inlineKeyboardButtonTypeBuy": "buy",
    "inlineKeyboardButtonTypeUser": "user", "inlineKeyboardButtonTypeWebApp": "webapp",
    "inlineKeyboardButtonTypeCopyText": "copy", "inlineKeyboardButtonTypeDisabled": "disabled",
}


def inline_rows(message: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """[[{text, kind, row, column, url}]] for QML; [] without an inline keyboard."""
    markup = message.get("reply_markup") or {}
    if markup.get("@type") != "replyMarkupInlineKeyboard":
        return []
    rows = []
    for r, buttons in enumerate(markup.get("rows") or []):
        row = []
        for c, button in enumerate(buttons):
            kind = button.get("type") or {}
            name = _KINDS.get(kind.get("@type", ""), "other")
            row.append({"text": button.get("text", ""), "kind": name, "row": r, "column": c,
                        "url": kind.get("url", ""), "supported": name in SUPPORTED})
        rows.append(row)
    return rows


def inline_button(message: dict[str, Any], row: int, column: int) -> dict[str, Any] | None:
    markup = message.get("reply_markup") or {}
    try:
        return markup.get("rows", [])[row][column]
    except (IndexError, TypeError):
        return None


def reply_keyboard(message: dict[str, Any] | None) -> dict[str, Any]:
    """{rows: [[text]], placeholder, oneTime, persistent, messageId} or {} (no keyboard)."""
    markup = (message or {}).get("reply_markup") or {}
    if markup.get("@type") != "replyMarkupShowKeyboard":
        return {}
    rows = [[b.get("text", "") for b in buttons
             if (b.get("type") or {}).get("@type", "keyboardButtonTypeText")
             == "keyboardButtonTypeText"]
            for buttons in markup.get("rows") or []]
    rows = [row for row in rows if row]
    if not rows:
        return {}
    return {"rows": rows, "placeholder": markup.get("input_field_placeholder", ""),
            "oneTime": bool(markup.get("one_time")),
            "persistent": bool(markup.get("is_persistent")),
            "messageId": (message or {}).get("id", 0)}
