"""Search: SQLite index, rank fusion, snippets, live indexing, backfill, hybrid search."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import numpy as np
from fakes import FakeEmbedder, FakeLib, error, new_chat, ok, qt_app, wait_until

from tgclient.services.search import SearchService, fuse, searchable_text
from tgclient.services.search_index import Doc, Progress, SearchIndex, fts_query
from tgclient.store.chats import ChatStore
from tgclient.store.users import UserStore
from tgclient.td import TdHub

CHAT = 42
OTHER = 50
SECRET = 43
NOW = int(time.time())


def msg(mid: int, text: str, chat: int = CHAT, sender: int = 5, ago: int = 0,
        **fields: Any) -> dict[str, Any]:
    return {"@type": "message", "id": mid, "chat_id": chat, "is_outgoing": False,
            "sender_id": {"@type": "messageSenderUser", "user_id": sender}, "date": NOW - ago,
            "content": {"@type": "messageText", "text": {"text": text}}, **fields}


class SearchIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.index = SearchIndex(":memory:")
        self.addCleanup(self.index.close)

    def test_keyword_prefix_filters_and_no_text_stored(self) -> None:
        self.index.upsert([
            Doc(CHAT, 1, "user:5", NOW, "Deployed the release to production"),
            Doc(CHAT, 2, "user:6", NOW, "Kdo má klíče od kanceláře?"),
            Doc(OTHER, 3, "user:5", NOW, "deploy tomorrow"),
        ])
        self.assertEqual(set(self.index.keyword("deplo")), {(CHAT, 1), (OTHER, 3)})
        self.assertEqual(self.index.keyword("deplo", chat_id=OTHER), [(OTHER, 3)])
        self.assertEqual(self.index.keyword("klice"), [(CHAT, 2)])  # diacritics folded
        self.assertEqual(self.index.keyword("deploy", sender="user:6"), [])
        self.assertEqual(self.index.keyword("\"(*"), [])  # junk input doesn't raise
        stored = self.index._db.execute("SELECT text FROM fts").fetchall()
        self.assertTrue(all(row[0] is None for row in stored), "message text must not be stored")

    def test_upsert_replaces_and_remove(self) -> None:
        self.index.upsert([Doc(CHAT, 1, "user:5", NOW, "old words")])
        self.index.upsert([Doc(CHAT, 1, "user:5", NOW, "new words")])
        self.assertEqual(self.index.keyword("old"), [])
        self.assertEqual(self.index.keyword("new"), [(CHAT, 1)])
        self.index.remove(CHAT, [1])
        self.assertEqual(self.index.keyword("new"), [])
        self.assertEqual(self.index.stats().docs, 0)

    def test_vectors_search_forget_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "search.sqlite3"
            index = SearchIndex(path)
            ids = index.upsert([Doc(CHAT, 1, "user:5", NOW, "a"), Doc(CHAT, 2, "user:6", NOW, "b")])
            index.load_vectors("m")
            vectors = np.eye(2, 4, dtype=np.float32)
            index.add_vectors("m", ids, vectors)
            query = np.array([1, 0, 0, 0], dtype=np.float32)
            self.assertEqual([k for k, _ in index.semantic(query)], [(CHAT, 1)])
            self.assertEqual(index.semantic(query, sender="user:6"), [])
            index.upsert([Doc(CHAT, 1, "user:5", NOW, "changed")])  # old vector is stale
            self.assertEqual(index.semantic(query), [])
            self.assertEqual(index.missing_vectors("m"), [(CHAT, 1)])
            index.set_progress(CHAT, Progress(10, 100, True))
            index.set_meta("semantic", "1")
            index.close()

            again = SearchIndex(path)
            again.load_vectors("m")
            second = np.array([0, 1, 0, 0], dtype=np.float32)
            self.assertEqual([k for k, _ in again.semantic(second)], [(CHAT, 2)])
            self.assertEqual(again.progress(CHAT), Progress(10, 100, True))
            self.assertEqual(again.meta("semantic"), "1")
            self.assertEqual(again.stats().vectors, 1)
            again.close()


class HelpersTest(unittest.TestCase):
    def test_fts_query(self) -> None:
        self.assertEqual(fts_query("Hello, world!"), '"hello"* "world"*')
        self.assertEqual(fts_query("  "), "")

    def test_fuse_prefers_agreement(self) -> None:
        a, b, c = (1, 1), (1, 2), (1, 3)
        self.assertEqual(fuse([[a, b], [b, c], [b]])[0], b)

    def test_searchable_text(self) -> None:
        self.assertEqual(searchable_text(msg(1, "  hi   there ")), "hi there")
        photo = {**msg(2, ""), "content": {"@type": "messagePhoto",
                                            "caption": {"text": "sunset"}}}
        self.assertEqual(searchable_text(photo), "sunset")
        voice = {**msg(3, ""), "content": {"@type": "messageVoiceNote"}}
        self.assertEqual(searchable_text(voice), "")
        self.assertEqual(searchable_text(voice, "see you"), "see you")

    def test_snippet_highlights_and_windows(self) -> None:
        qt_app()
        from tgclient.models.search import snippet_html

        text = "x " * 100 + "the Deployment went fine <b>"
        out = snippet_html(text, "deploy", "#123456", limit=60)
        self.assertTrue(out.startswith("…"))
        self.assertIn('<b><font color="#123456">Deployment</font></b>', out)
        self.assertIn("&lt;b&gt;", out)


class Server:
    def __init__(self) -> None:
        self.history = {CHAT: [msg(i, f"history message {i}", ago=(30 - i) * 60)
                               for i in range(30, 0, -1)]}
        self.server_hits: list[dict[str, Any]] = []
        self.missing: set[int] = set()
        self.texts: dict[int, str] = {}

    def __call__(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        match req["@type"]:
            case "getChatHistory":
                start = req["from_message_id"]
                page = [m for m in self.history.get(req["chat_id"], [])
                        if not start or m["id"] < start][:req["limit"]]
                return [{"@type": "messages", "total_count": len(page), "messages": page,
                         "@extra": req["@extra"]}]
            case "getMessages":
                found = [None if i in self.missing else msg(
                    i, self.texts.get(i, f"history message {i}"), chat=req["chat_id"])
                         for i in req["message_ids"]]
                return [{"@type": "messages", "total_count": len(found), "messages": found,
                         "@extra": req["@extra"]}]
            case "getMessage":
                return [{**msg(req["message_id"], "fetched"), "@extra": req["@extra"]}]
            case "searchMessages" | "searchChatMessages":
                return [{"@type": "foundMessages", "total_count": len(self.server_hits),
                         "messages": self.server_hits, "next_offset": "",
                         "@extra": req["@extra"]}]
            case "loadChats":
                return [error(req, 404, "Not Found")]
        return [ok(req)]


class SearchServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = Server()
        self.lib = FakeLib(self.server)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.index = SearchIndex(":memory:")
        self.embedder = FakeEmbedder()
        self.search = SearchService(self.client, self.chats, self.users, self.index,
                                    self.embedder)
        self.search.start_delay = 3600  # backfill only where a test starts it
        self.search.flush_delay = 0
        self.search.pace = 0
        self.lib.push(new_chat(CHAT, "Team", 10))
        self.lib.push(new_chat(OTHER, "Friends", 9))
        self.lib.push(new_chat(SECRET, "Secret", 8, "chatTypeSecret"))
        self.lib.push({"@type": "updateUser", "user": {"id": 5, "first_name": "Olena"}})
        await wait_until(lambda: SECRET in self.chats.chats and 5 in self.users.users)
        self.search.start()

    async def asyncTearDown(self) -> None:
        await self.search.close()

    async def test_live_indexing_skips_secret_chats_and_follows_deletes(self) -> None:
        self.lib.push({"@type": "updateNewMessage", "message": msg(100, "Release is deployed")})
        self.lib.push({"@type": "updateNewMessage",
                       "message": msg(101, "deployed secretly", chat=SECRET)})
        await wait_until(lambda: self.search.status().docs == 1)
        self.assertEqual(self.index.keyword("deployed"), [(CHAT, 100)])
        self.lib.push({"@type": "updateDeleteMessages", "chat_id": CHAT, "message_ids": [100],
                       "is_permanent": True, "from_cache": False})
        await wait_until(lambda: self.search.status().docs == 0)

    async def test_backfill_walks_history_and_finishes(self) -> None:
        self.search.start_delay = 0
        self.search._started = False
        self.search.start()
        await wait_until(lambda: self.index.progress(CHAT).done, timeout=5)
        await wait_until(lambda: self.search.status().docs == 30)
        self.assertEqual(self.index.progress(CHAT).count, 30)
        self.assertTrue(self.index.keyword("history", chat_id=CHAT))

    async def test_search_merges_server_hits_and_drops_deleted(self) -> None:
        self.index.upsert([Doc(CHAT, 7, "user:5", NOW, "history message 7"),
                           Doc(CHAT, 8, "user:5", NOW, "history message 8")])
        self.server.missing = {8}
        self.server.server_hits = [msg(900, "history from the server", chat=OTHER)]
        hits = await self.search.search("history")
        keys = [(h.chat_id, h.message_id) for h in hits]
        self.assertIn((CHAT, 7), keys)
        self.assertIn((OTHER, 900), keys)
        self.assertNotIn((CHAT, 8), keys)
        self.assertEqual(next(h for h in hits if h.message_id == 7).sender, "Olena")
        await wait_until(lambda: self.index.keyword("history", chat_id=CHAT) == [(CHAT, 7)])

    async def test_meaning_search_across_languages(self) -> None:
        self.server.missing = set()
        self.search.set_semantic(True)
        await wait_until(lambda: self.search.status().model_state == "ready")
        self.assertTrue(self.embedder.loaded)
        for i, text in enumerate(["Kdo má klíče?",
                                  "Встречаемся в пятницу",
                                  "Купил билеты в Вену на субботу"], start=200):
            self.lib.push({"@type": "updateNewMessage", "message": msg(i, text)})
        await wait_until(lambda: self.search.status().vectors == 3)
        hits = await self.search.search("ключи офис")
        self.assertEqual((hits[0].chat_id, hits[0].message_id), (CHAT, 200))
        self.assertTrue(hits[0].semantic and not hits[0].keyword)
        self.assertEqual(self.index.meta("semantic"), "1")

    async def test_backlog_is_embedded_after_turning_meaning_on(self) -> None:
        self.index.upsert([Doc(CHAT, 7, "user:5", NOW, "history message 7"),
                           Doc(CHAT, 9, "user:5", NOW, "ok")])
        self.server.texts = {9: "ok"}
        self.search.set_semantic(True)
        await wait_until(lambda: self.search.status().vectors == 2)  # "ok": zero vector
        self.assertEqual(self.index.missing_vectors(self.embedder.model), [])


if __name__ == "__main__":
    unittest.main()
