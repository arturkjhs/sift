"""SQLite storage for AI data: per-chat switches, transcripts, translations, results (summaries,
answers, digests), spending, and a cache of identical requests.

Messages themselves are never stored here (TDLib is the source of truth); results and
translations are model output the user asked for. Small tables are read into memory on open,
so lookups from Qt models are cheap; writes are meant to run off the event loop (AiService wraps
them in asyncio.to_thread), hence the lock.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id INTEGER PRIMARY KEY,
    ai_enabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chat_flags (
    chat_id INTEGER NOT NULL,
    flag TEXT NOT NULL,  -- digest | smart_notify: background features, each switched on alone
    PRIMARY KEY (chat_id, flag)
);
CREATE TABLE IF NOT EXISTS transcripts (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    model TEXT NOT NULL,
    created INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS translations (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    lang TEXT NOT NULL,
    text TEXT NOT NULL,
    created INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id, lang)
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
CREATE TABLE IF NOT EXISTS results (
    chat_id INTEGER NOT NULL,  -- 0: across chats (digest, promises)
    subject TEXT NOT NULL,
    scope TEXT NOT NULL,
    name TEXT NOT NULL,
    question TEXT NOT NULL,
    text TEXT NOT NULL,
    model TEXT NOT NULL,
    created INTEGER NOT NULL,
    cost REAL NOT NULL,
    data TEXT NOT NULL,  -- JSON, e.g. extracted events
    PRIMARY KEY (chat_id, subject)
);
CREATE TABLE IF NOT EXISTS usage (
    created INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    feature TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_created ON usage (created);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SummaryKey = tuple[int, str]  # (chat_id, subject): "" = the whole chat, "user:<id>" = a person


@dataclass(frozen=True)
class StoredSummary:
    scope: str  # unread | day | week | person | ask | events | answers | doc | digest | ...
    text: str  # markdown with tgc://message/... links
    model: str
    created: int
    name: str = ""  # who or what it is about
    question: str = ""
    cost: float = 0.0
    data: str = ""  # JSON


def month_start(now: float | None = None) -> int:
    today = datetime.fromtimestamp(now or time.time())  # noqa: DTZ006 - local months
    return int(today.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())


class AiStore:
    CACHE_DAYS = 14

    def __init__(self, path: Path | str, initial: bytes | None = None) -> None:
        """`initial`: a database image to start from (path ":memory:"), e.g. decrypted."""
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        if initial:
            self._db.deserialize(initial)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.execute("DELETE FROM cache WHERE created < ?",
                             (int(time.time()) - self.CACHE_DAYS * 86400,))
        self.enabled: set[int] = {
            row[0] for row in self._db.execute(
                "SELECT chat_id FROM chat_settings WHERE ai_enabled = 1")
        }
        self.flags: dict[str, set[int]] = {"digest": set(), "smart_notify": set()}
        for chat_id, flag in self._db.execute("SELECT chat_id, flag FROM chat_flags"):
            self.flags.setdefault(flag, set()).add(chat_id)
        self.transcripts: dict[tuple[int, int], str] = {
            (chat_id, message_id): text for chat_id, message_id, text in self._db.execute(
                "SELECT chat_id, message_id, text FROM transcripts")
        }
        self.translations: dict[tuple[int, int, str], str] = {
            (row[0], row[1], row[2]): row[3] for row in self._db.execute(
                "SELECT chat_id, message_id, lang, text FROM translations")
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
        self.summaries.update({
            (row[0], row[1]): StoredSummary(
                scope=row[2], name=row[3], question=row[4], text=row[5], model=row[6],
                created=row[7], cost=row[8], data=row[9])
            for row in self._db.execute(
                "SELECT chat_id, subject, scope, name, question, text, model, created, cost, "
                "data FROM results")
        })
        self.month = month_start()
        self.spent: dict[int, float] = {  # chat id -> USD this month (0: across chats)
            chat_id: total for chat_id, total in self._db.execute(
                "SELECT chat_id, SUM(cost) FROM usage WHERE created >= ? GROUP BY chat_id",
                (self.month,))
        }
        self.values: dict[str, str] = dict(self._db.execute("SELECT name, value FROM kv"))

    # In-memory state is updated by the caller first; these persist it.

    def save_enabled(self, chat_id: int, enabled: bool) -> None:
        self._write(
            "INSERT INTO chat_settings (chat_id, ai_enabled) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET ai_enabled = excluded.ai_enabled",
            (chat_id, int(enabled)))

    def save_flag(self, chat_id: int, flag: str, on: bool) -> None:
        if on:
            self._write("INSERT OR IGNORE INTO chat_flags VALUES (?, ?)", (chat_id, flag))
        else:
            self._write("DELETE FROM chat_flags WHERE chat_id = ? AND flag = ?", (chat_id, flag))

    def save_transcript(self, chat_id: int, message_id: int, text: str, model: str) -> None:
        self._write(
            "INSERT OR REPLACE INTO transcripts VALUES (?, ?, ?, ?, ?)",
            (chat_id, message_id, text, model, int(time.time())))

    def save_translation(self, chat_id: int, message_id: int, lang: str, text: str) -> None:
        self._write("INSERT OR REPLACE INTO translations VALUES (?, ?, ?, ?, ?)",
                    (chat_id, message_id, lang, text, int(time.time())))

    def save_summary(self, key: SummaryKey, summary: StoredSummary) -> None:
        chat_id, subject = key
        self._write(
            "INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, subject, summary.scope, summary.name, summary.question, summary.text,
             summary.model, summary.created, summary.cost, summary.data))

    def add_usage(self, chat_id: int, feature: str, model: str, prompt_tokens: int,
                  completion_tokens: int, cost: float, created: int | None = None) -> None:
        self._write("INSERT INTO usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (created or int(time.time()), chat_id, feature, model, prompt_tokens,
                     completion_tokens, cost))

    def cache_get(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT text FROM cache WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def cache_put(self, key: str, text: str) -> None:
        self._write("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, text, int(time.time())))

    def save_value(self, name: str, value: str) -> None:
        self._write("INSERT OR REPLACE INTO kv VALUES (?, ?)", (name, value))

    def forget_chat(self, chat_id: int) -> None:
        """Delete everything AI produced for a chat (transcripts, translations, results)."""
        with self._lock, self._db:
            for table in ("transcripts", "translations", "summaries", "person_summaries",
                          "results", "chat_flags"):
                self._db.execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))

    def snapshot(self) -> bytes:
        """The whole database as bytes (to store it encrypted)."""
        with self._lock:
            return self._db.serialize()

    @property
    def changes(self) -> int:
        return self._db.total_changes

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _write(self, sql: str, params: tuple[object, ...]) -> None:
        with self._lock, self._db:
            self._db.execute(sql, params)
