"""SQLite storage for AI data: per-chat switches, voice transcripts, the latest summary per chat.

Messages themselves are never stored here (TDLib is the source of truth). Everything is read
into memory on open, so lookups from Qt models are cheap; writes are meant to run off the event
loop (AiService wraps them in asyncio.to_thread), hence the lock.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id INTEGER PRIMARY KEY,
    ai_enabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transcripts (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    model TEXT NOT NULL,
    created INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS summaries (
    chat_id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL,
    text TEXT NOT NULL,
    model TEXT NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS person_summaries (
    chat_id INTEGER NOT NULL,
    sender TEXT NOT NULL,
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    model TEXT NOT NULL,
    created INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender)
);
"""

SummaryKey = tuple[int, str]  # (chat_id, subject): "" = the whole chat, "user:<id>" = a person


@dataclass(frozen=True)
class StoredSummary:
    scope: str  # unread | day | week | person
    text: str  # markdown with tgc://message/<id> links
    model: str
    created: int
    name: str = ""  # the person, for person summaries


class AiStore:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
        self.enabled: set[int] = {
            row[0] for row in self._db.execute(
                "SELECT chat_id FROM chat_settings WHERE ai_enabled = 1")
        }
        self.transcripts: dict[tuple[int, int], str] = {
            (chat_id, message_id): text for chat_id, message_id, text in self._db.execute(
                "SELECT chat_id, message_id, text FROM transcripts")
        }
        self.summaries: dict[SummaryKey, StoredSummary] = {
            (row[0], ""): StoredSummary(*row[1:]) for row in self._db.execute(
                "SELECT chat_id, scope, text, model, created FROM summaries")
        }
        self.summaries.update({
            (row[0], row[1]): StoredSummary("person", row[3], row[4], row[5], name=row[2])
            for row in self._db.execute(
                "SELECT chat_id, sender, name, text, model, created FROM person_summaries")
        })

    # In-memory state is updated by the caller first; these persist it.

    def save_enabled(self, chat_id: int, enabled: bool) -> None:
        self._write(
            "INSERT INTO chat_settings (chat_id, ai_enabled) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET ai_enabled = excluded.ai_enabled",
            (chat_id, int(enabled)))

    def save_transcript(self, chat_id: int, message_id: int, text: str, model: str) -> None:
        self._write(
            "INSERT OR REPLACE INTO transcripts VALUES (?, ?, ?, ?, ?)",
            (chat_id, message_id, text, model, int(time.time())))

    def save_summary(self, key: SummaryKey, summary: StoredSummary) -> None:
        chat_id, subject = key
        if subject:
            self._write(
                "INSERT OR REPLACE INTO person_summaries VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, subject, summary.name, summary.text, summary.model, summary.created))
        else:
            self._write(
                "INSERT OR REPLACE INTO summaries VALUES (?, ?, ?, ?, ?)",
                (chat_id, summary.scope, summary.text, summary.model, summary.created))

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _write(self, sql: str, params: tuple[object, ...]) -> None:
        with self._lock, self._db:
            self._db.execute(sql, params)
