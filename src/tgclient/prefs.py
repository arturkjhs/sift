"""User preferences changed from the UI (theme, notifications): a small JSON file in data_dir.

Writes happen on user clicks only (never from update handlers), so a tiny synchronous write is
fine. A missing or broken file means defaults.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "theme": "system",
    "notifications": True,
    "notification_preview": True,
    "notification_sound": True,  # off: every notification is silent
    "auto_lock_minutes": 0,  # with a passcode: lock after this long without input (0: never)
    # AI: empty model names mean the defaults from config.py (or TGC_*_MODEL).
    "summary_model": "",
    "cheap_model": "",
    "transcription_model": "",
    "ai_monthly_limit": 1.0,  # USD per chat and month; 0 = no limit
    # The user's language for everything AI writes to them ("" = the first supported language
    # of the system UI, else English). "translate_to" is the older name of the same setting.
    "ai_language": "",
    "translate_to": "",
    # Other languages the user reads (codes): suggested replies in them get no translation.
    "read_languages": [],
    "auto_update": True,  # check GitHub Releases daily (packaged builds only)
}


class Prefs:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._values: dict[str, Any] = {}
        if path is not None and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._values = loaded
            except (OSError, ValueError) as e:
                log.warning("Ignoring unreadable %s: %s", path, e)

    def get(self, name: str) -> Any:
        return self._values.get(name, DEFAULTS.get(name))

    def set(self, name: str, value: Any) -> None:
        if self._values.get(name) == value:
            return
        self._values[name] = value
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._values, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as e:
            log.warning("Saving preferences failed: %s", e)
