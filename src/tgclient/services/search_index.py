"""Local search index in SQLite: FTS5 tokens and embedding vectors, no message text.

TDLib stays the source of truth for messages: the FTS table is contentless (only the inverted
index is stored) and results are resolved back to messages through TDLib. Vectors are kept in
memory as one normalized float32 matrix (brute-force cosine is milliseconds at personal scale)
and stored as float16 blobs.

All methods are blocking and thread-safe: call them through asyncio.to_thread.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sender TEXT NOT NULL,
    date INTEGER NOT NULL,
    UNIQUE (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS docs_by_sender ON docs (chat_id, sender);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    text, content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS vectors (
    id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    vec BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS backfill (
    chat_id INTEGER PRIMARY KEY,
    oldest_id INTEGER NOT NULL,
    count INTEGER NOT NULL,
    done INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_TOKEN = re.compile(r"\w+", re.UNICODE)
MAX_QUERY_TOKENS = 8

Key = tuple[int, int]  # (chat_id, message_id)


@dataclass(frozen=True)
class Doc:
    chat_id: int
    message_id: int
    sender: str  # "user:<id>" | "chat:<id>"
    date: int
    text: str


@dataclass(frozen=True)
class Progress:
    oldest_id: int = 0
    count: int = 0
    done: bool = False


@dataclass(frozen=True)
class Stats:
    docs: int
    chats: int
    vectors: int


def fts_query(query: str) -> str:
    """User text -> FTS5 query: every word must match, as a prefix ("deplo" finds "deploy")."""
    tokens = _TOKEN.findall(query.lower())[:MAX_QUERY_TOKENS]
    return " ".join(f'"{t}"*' for t in tokens)


class SearchIndex:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
        # In-memory vectors of one model: row i of the matrix belongs to doc id _ids[i].
        self._model = ""
        self._ids: list[int] = []
        self._rows: dict[int, int] = {}
        self._chunks: list[np.ndarray] = []
        self._matrix = np.zeros((0, 0), dtype=np.float32)
        self._alive = np.zeros(0, dtype=bool)

    # --- writes -----------------------------------------------------------------------------

    def upsert(self, docs: list[Doc]) -> list[int]:
        """Index messages (replacing earlier versions). Returns their doc ids, in order."""
        ids: list[int] = []
        with self._lock, self._db:
            for doc in docs:
                row = self._db.execute(
                    "SELECT id FROM docs WHERE chat_id = ? AND message_id = ?",
                    (doc.chat_id, doc.message_id)).fetchone()
                if row is not None:
                    doc_id = row[0]
                    self._db.execute("DELETE FROM fts WHERE rowid = ?", (doc_id,))
                    self._db.execute("DELETE FROM vectors WHERE id = ?", (doc_id,))
                    self._db.execute("UPDATE docs SET sender = ?, date = ? WHERE id = ?",
                                     (doc.sender, doc.date, doc_id))
                    self._forget_vector(doc_id)
                else:
                    doc_id = self._db.execute(
                        "INSERT INTO docs (chat_id, message_id, sender, date) VALUES (?, ?, ?, ?)",
                        (doc.chat_id, doc.message_id, doc.sender, doc.date)).lastrowid or 0
                self._db.execute("INSERT INTO fts (rowid, text) VALUES (?, ?)", (doc_id, doc.text))
                ids.append(doc_id)
        return ids

    def remove(self, chat_id: int, message_ids: list[int]) -> None:
        with self._lock, self._db:
            for message_id in message_ids:
                row = self._db.execute(
                    "SELECT id FROM docs WHERE chat_id = ? AND message_id = ?",
                    (chat_id, message_id)).fetchone()
                if row is None:
                    continue
                self._db.execute("DELETE FROM fts WHERE rowid = ?", (row[0],))
                self._db.execute("DELETE FROM vectors WHERE id = ?", (row[0],))
                self._db.execute("DELETE FROM docs WHERE id = ?", (row[0],))
                self._forget_vector(row[0])

    def add_vectors(self, model: str, ids: list[int], vectors: np.ndarray) -> None:
        if not ids:
            return
        with self._lock, self._db:
            # Skip docs removed while their vectors were being computed.
            known = {r[0] for r in self._db.execute(
                f"SELECT id FROM docs WHERE id IN ({','.join('?' * len(ids))})", ids)}
            keep = [i for i, doc_id in enumerate(ids) if doc_id in known]
            self._db.executemany(
                "INSERT OR REPLACE INTO vectors (id, model, vec) VALUES (?, ?, ?)",
                [(ids[i], model, vectors[i].astype(np.float16).tobytes()) for i in keep])
            if model == self._model and keep:
                self._append([ids[i] for i in keep], vectors[keep])

    def set_progress(self, chat_id: int, progress: Progress) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO backfill (chat_id, oldest_id, count, done) "
                "VALUES (?, ?, ?, ?)",
                (chat_id, progress.oldest_id, progress.count, int(progress.done)))

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                             (key, value))

    # --- reads ------------------------------------------------------------------------------

    def progress(self, chat_id: int) -> Progress:
        with self._lock:
            row = self._db.execute("SELECT oldest_id, count, done FROM backfill WHERE chat_id = ?",
                                   (chat_id,)).fetchone()
        return Progress(row[0], row[1], bool(row[2])) if row else Progress()

    def meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def stats(self) -> Stats:
        with self._lock:
            docs, chats = self._db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT chat_id) FROM docs").fetchone()
            vectors = self._db.execute("SELECT COUNT(*) FROM vectors WHERE model = ?",
                                       (self._model,)).fetchone()[0]
        return Stats(docs, chats, vectors)

    def keyword(self, query: str, chat_id: int = 0, sender: str = "",
                limit: int = 100) -> list[Key]:
        match = fts_query(query)
        if not match:
            return []
        sql = ("SELECT d.chat_id, d.message_id FROM fts JOIN docs d ON d.id = fts.rowid "
               "WHERE fts MATCH ?")
        params: list[object] = [match]
        if chat_id:
            sql += " AND d.chat_id = ?"
            params.append(chat_id)
        if sender:
            sql += " AND d.sender = ?"
            params.append(sender)
        sql += " ORDER BY bm25(fts) LIMIT ?"
        params.append(limit)
        with self._lock:
            try:
                return [(r[0], r[1]) for r in self._db.execute(sql, params)]
            except sqlite3.OperationalError:
                return []  # malformed query

    def missing_vectors(self, model: str, limit: int = 64) -> list[Key]:
        with self._lock:
            return [(r[0], r[1]) for r in self._db.execute(
                "SELECT d.chat_id, d.message_id FROM docs d "
                "LEFT JOIN vectors v ON v.id = d.id AND v.model = ? "
                "WHERE v.id IS NULL ORDER BY d.date DESC LIMIT ?", (model, limit))]

    def doc_ids(self, keys: list[Key]) -> list[int | None]:
        with self._lock:
            return [
                (row[0] if (row := self._db.execute(
                    "SELECT id FROM docs WHERE chat_id = ? AND message_id = ?", key).fetchone())
                 else None)
                for key in keys
            ]

    # --- vectors ----------------------------------------------------------------------------

    def load_vectors(self, model: str) -> None:
        """Make `model`'s stored vectors searchable (drops other models from memory)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, vec FROM vectors WHERE model = ?", (model,)).fetchall()
            self._model = model
            self._ids, self._rows, self._chunks = [], {}, []
            self._matrix = np.zeros((0, 0), dtype=np.float32)
            self._alive = np.zeros(0, dtype=bool)
            if rows:
                matrix = np.stack([np.frombuffer(r[1], dtype=np.float16) for r in rows])
                self._append([r[0] for r in rows], matrix.astype(np.float32))

    def semantic(self, vector: np.ndarray, chat_id: int = 0, sender: str = "",
                 limit: int = 100, min_score: float = 0.3) -> list[tuple[Key, float]]:
        with self._lock:
            matrix = self._materialize()
            if not len(matrix):
                return []
            scores = matrix @ vector.astype(np.float32)
            scores[~self._alive] = -1.0
            # Over-fetch: chat/sender filters are applied afterwards.
            ranked = np.argsort(-scores)[: limit * (8 if chat_id or sender else 1)]
            top = [int(i) for i in ranked if scores[i] >= min_score]
            if not top:
                return []
            ids = [self._ids[i] for i in top]
            rows = {r[0]: r for r in self._db.execute(
                f"SELECT id, chat_id, message_id, sender FROM docs "
                f"WHERE id IN ({','.join('?' * len(ids))})", ids)}
        result: list[tuple[Key, float]] = []
        for i, doc_id in zip(top, ids, strict=True):
            row = rows.get(doc_id)
            if row is None or (chat_id and row[1] != chat_id) or (sender and row[3] != sender):
                continue
            result.append(((row[1], row[2]), float(scores[i])))
            if len(result) >= limit:
                break
        return result

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _append(self, ids: list[int], vectors: np.ndarray) -> None:
        start = len(self._ids)
        for offset, doc_id in enumerate(ids):
            self._forget_vector(doc_id)  # re-embedded: the old row must not match anymore
            self._rows[doc_id] = start + offset
        self._ids.extend(ids)
        self._chunks.append(np.asarray(vectors, dtype=np.float32))
        self._alive = np.concatenate([self._alive, np.ones(len(ids), dtype=bool)])

    def _materialize(self) -> np.ndarray:
        if self._chunks:
            parts = ([self._matrix] if self._matrix.size else []) + self._chunks
            self._matrix = np.concatenate(parts)
            self._chunks = []
        return self._matrix

    def _forget_vector(self, doc_id: int) -> None:
        row = self._rows.pop(doc_id, None)
        if row is not None:
            self._alive[row] = False
