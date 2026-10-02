"""M8: assistant features in AiService (translation, questions, dates, answers, documents,
digest, promises, reply suggestions, smart notifications), spending and the cache, helpers."""

from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import mock

from fakes import FakeLib, FakeRouter, error, new_chat, ok, qt_app, wait_until

from tgclient.services import assist, summary
from tgclient.services.ai import AiService, AiUnavailable
from tgclient.services.ai_store import AiStore, StoredSummary
from tgclient.services.search import Hit
from tgclient.store.chats import ChatStore
from tgclient.store.markdown import markdown_to_html
from tgclient.store.richtext import Palette
from tgclient.store.users import UserStore
from tgclient.td import TdHub

CHAT = 42
GROUP = 50
OTHER = 60
SECRET = 43
ME = 1
NOW = int(time.time())


def msg(mid: int, text: str, sender: int = 5, chat: int = CHAT, ago: int = 0,
        **fields: Any) -> dict[str, Any]:
    return {"@type": "message", "id": mid, "chat_id": chat, "is_outgoing": sender == ME,
            "sender_id": {"@type": "messageSenderUser", "user_id": sender}, "date": NOW - ago,
            "content": {"@type": "messageText", "text": {"text": text, "entities": []}},
            **fields}


def reply(message_id: int) -> dict[str, Any]:
    return {"@type": "messageReplyToMessage", "chat_id": CHAT, "message_id": message_id}


class Server:
    """Scripted TDLib: per-chat histories (newest first), members, files."""

    def __init__(self, doc_path: str) -> None:
        self.doc_path = doc_path
        self.history: dict[int, list[dict[str, Any]]] = {
            CHAT: [msg(5, "ok see you then", 5, ago=60, reply_to=reply(4)),
                   msg(4, "Friday 18:00 at Lucerna?", ME, ago=120, reply_to=reply(3)),
                   msg(3, "let's meet on friday", 5, ago=180),
                   msg(2, "the contract is in the drive", 6, ago=240), msg(1, "hi", 5, ago=300)],
            GROUP: [msg(24, "no, I'm away", 7, GROUP, ago=10), msg(23, "yes!", 6, GROUP, ago=20),
                    msg(22, "unrelated meme", 6, GROUP, ago=30),
                    msg(21, "who comes on Saturday?", 5, GROUP, ago=40)],
            OTHER: [msg(31, "I'll send the invoice tomorrow", ME, OTHER, ago=50),
                    msg(30, "can you send the invoice?", 8, OTHER, ago=100)],
        }
        self.doc = {"@type": "messageDocument", "document": {
            "file_name": "terms.pdf", "mime_type": "application/pdf", "document": {
                "@type": "file", "id": 700, "size": 8, "expected_size": 8,
                "local": {"path": doc_path, "is_downloading_completed": True}, "remote": {}}},
            "caption": {"text": ""}}

    def __call__(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        extra = req.get("@extra")
        match req["@type"]:
            case "getChatHistory":
                history = self.history.get(req["chat_id"], [])
                start, offset = req["from_message_id"], req["offset"]
                if offset < 0:  # up to -offset newer messages, then from_message_id and older
                    newer = [m for m in history if m["id"] > start][::-1][:-offset][::-1]
                    older = [m for m in history if m["id"] <= start]
                    page = (newer + older)[:req["limit"]]
                else:
                    page = [m for m in history if not start or m["id"] < start
                            or (req.get("only_local") and m["id"] <= start)][:req["limit"]]
                return [{"@type": "messages", "total_count": len(page), "messages": page,
                         "@extra": extra}]
            case "getMessage":
                if req["message_id"] == 99:
                    return [{**msg(99, ""), "content": self.doc, "@extra": extra}]
                for history in self.history.values():
                    for m in history:
                        if m["id"] == req["message_id"] and m["chat_id"] == req["chat_id"]:
                            return [{**m, "@extra": extra}]
                return [error(req, 404, "Not Found")]
            case "getRepliedMessage":
                for history in self.history.values():
                    for m in history:
                        if m["id"] == req["message_id"] and m.get("reply_to"):
                            target = m["reply_to"]["message_id"]
                            found = [x for x in history if x["id"] == target]
                            if found:
                                return [{**found[0], "@extra": extra}]
                return [error(req, 404, "Not Found")]
            case "searchChatMessages":
                sender = (req.get("sender_id") or {}).get("user_id")
                mine = [m for h in self.history.values() for m in h
                        if m["chat_id"] == req["chat_id"]
                        and m["sender_id"]["user_id"] == sender]
                return [{"@type": "foundChatMessages", "total_count": len(mine),
                         "messages": mine, "next_from_message_id": 0, "@extra": extra}]
            case "searchChatMembers":
                return [{"@type": "chatMembers", "total_count": 4, "@extra": extra, "members": [
                    {"member_id": {"@type": "messageSenderUser", "user_id": u}}
                    for u in (5, 6, 7, 8)]}]
            case "downloadFile":
                return [{**self.doc["document"]["document"], "@extra": extra}]
        return [ok(req)]


class HelpersTest(unittest.TestCase):
    def test_parse_events_and_ics(self) -> None:
        reply = ('Here:\n```json\n{"events": [{"title": "Dinner, Lucerna", "start": '
                 '"2026-03-13 18:00", "end": null, "location": "Lucerna", "ref": "m4"}, '
                 '{"title": "Deadline", "start": "2026-03-20", "ref": "m9"}, '
                 '{"title": "broken", "start": "someday"}]}\n```')
        events = assist.parse_events(reply)
        self.assertEqual([(e.title, e.start, e.ref) for e in events],
                         [("Dinner, Lucerna", "2026-03-13T18:00", 4),
                          ("Deadline", "2026-03-20", 9)])
        ics = assist.to_ics(events, now=datetime(2026, 3, 1))  # noqa: DTZ001
        self.assertIn("DTSTART:20260313T180000\r\nDTEND:20260313T190000", ics)
        self.assertIn("SUMMARY:Dinner\\, Lucerna", ics)
        self.assertIn("DTSTART;VALUE=DATE:20260320\r\nDTEND;VALUE=DATE:20260321", ics)
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n"))
        self.assertIn("[m4]", assist.events_markdown(events))
        self.assertEqual(assist.parse_events("no json here"), [])

    def test_multi_chat_refs_and_links(self) -> None:
        blocks = [assist.ChatBlock(7, "Team", [msg(10, "ship it"), msg(11, "done", ME)]),
                  assist.ChatBlock(8, "Family", [msg(10, "dinner?")])]
        text, source = assist.render_multi(blocks, lambda m: "Ann", lambda m: None,
                                           mine=lambda m: m["is_outgoing"])
        self.assertIn("## Team\n[m1]", text)
        self.assertIn("[m2] (me)", text)
        self.assertIn("## Family\n[m3]", text)
        linked = summary.linkify("- dinner [m3]", source)
        self.assertIn("(tgc://message/8/10)", linked)
        self.assertEqual(summary.parse_message_link("tgc://message/8/10"), (8, 10))
        self.assertEqual(summary.parse_message_link("tgc://message/10"), (0, 10))
        self.assertEqual(summary.parse_message_link("https://x"), (0, 0))

    def test_small_helpers(self) -> None:
        self.assertTrue(assist.is_yes("Yes."))
        self.assertFalse(assist.is_yes("no"))
        self.assertEqual(assist.document_kind("a.PDF", ""), "pdf")
        self.assertEqual(assist.document_kind("notes", "text/plain"), "text")
        self.assertEqual(assist.document_kind("a.zip", "application/zip"), "")
        parts = assist.document_messages("what?", "a.pdf", "pdf", b"%PDF")[1]["content"]
        self.assertEqual(parts[0]["file"]["file_data"],
                         "data:application/pdf;base64," + base64.b64encode(b"%PDF").decode())
        html = markdown_to_html("| A | B |\n|---|---|\n| x | **y** |", Palette(mono="m"))
        self.assertIn("<th align=\"left\">A</th>", html)
        self.assertIn("<td align=\"left\"><b>y</b></td>", html)


class OpenRouterUsageTest(unittest.IsolatedAsyncioTestCase):
    async def test_cost_and_plugins(self) -> None:
        router = FakeRouter("hi", cost=0.0021)
        client = router.client()
        reply = await client.complete("m/x", [], plugins=assist.PDF_PLUGINS)
        await client.aclose()
        self.assertEqual((reply.text, reply.cost, reply.prompt_tokens), ("hi", 0.0021, 100))
        self.assertEqual(router.requests[0]["usage"], {"include": True})
        self.assertEqual(router.requests[0]["plugins"], assist.PDF_PLUGINS)


class StreamCleanupTest(unittest.TestCase):
    """The app runs on qasync, which (unlike asyncio.run) installs no async-generator hooks:
    a half-read httpx stream then printed "async generator ignored GeneratorExit"."""

    def test_stream_leaves_no_half_read_generators(self) -> None:
        import asyncio
        import gc
        import sys
        from unittest import mock

        from tgclient.services import openrouter

        async def server(reader: Any, writer: Any) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n"
                         b"transfer-encoding: chunked\r\n\r\n")

            def chunk(text: str) -> None:
                data = text.encode()
                writer.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")

            for part in ("Hello ", "world"):
                chunk("data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n")
                await writer.drain()
            chunk("data: [DONE]\n\n")
            await writer.drain()
            await asyncio.sleep(0.1)  # the body goes on after [DONE], like OpenRouter's
            chunk(": end\n\n")
            writer.write(b"0\r\n\r\n")
            await writer.drain()
            writer.close()

        async def main() -> str:
            sys.set_asyncgen_hooks(None, None)  # like qasync
            listener = await asyncio.start_server(server, "127.0.0.1", 0)
            port = listener.sockets[0].getsockname()[1]
            with mock.patch.object(openrouter, "API_URL", f"http://127.0.0.1:{port}/"):
                client = openrouter.OpenRouter("k")
                reply = await client.stream("m", [], lambda text: None)
                await client.aclose()
            listener.close()
            return reply.text

        unraisable: list[Any] = []
        previous = sys.unraisablehook
        sys.unraisablehook = unraisable.append
        try:
            text = asyncio.run(main())
            gc.collect()
        finally:
            sys.unraisablehook = previous
        self.assertEqual(text, "Hello world")
        self.assertEqual([repr(u.exc_value) for u in unraisable], [])


class AiStoreM8Test(unittest.TestCase):
    def test_persists_new_tables_and_forgets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai.sqlite3"
            store = AiStore(path)
            store.save_flag(1, "digest", True)
            store.save_flag(2, "smart_notify", True)
            store.save_flag(2, "smart_notify", False)
            store.save_translation(1, 5, "en", "hello")
            store.save_summary((1, "ask"), StoredSummary("ask", "- a", "m", 5, question="q?",
                                                         cost=0.01, data='{"x": 1}'))
            store.add_usage(1, "ask", "m", 10, 2, 0.01)
            store.add_usage(1, "ask", "m", 10, 2, 0.02)
            store.add_usage(1, "ask", "m", 10, 2, 5.0, created=1)  # long ago: not this month
            store.cache_put("k", "cached")
            store.save_value("digest_since", "123")
            store.close()
            again = AiStore(path)
            self.assertEqual(again.flags["digest"], {1})
            self.assertEqual(again.flags["smart_notify"], set())
            self.assertEqual(again.translations, {(1, 5, "en"): "hello"})
            self.assertEqual(again.summaries[(1, "ask")].question, "q?")
            self.assertAlmostEqual(again.spent[1], 0.03)
            self.assertEqual(again.cache_get("k"), "cached")
            self.assertEqual(again.values["digest_since"], "123")
            again.forget_chat(1)
            again.close()
            third = AiStore(path)
            self.assertEqual((third.flags["digest"], third.translations), (set(), {}))
            self.assertNotIn((1, "ask"), third.summaries)
            third.close()


class AssistCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        doc = Path(tmp.name) / "terms.pdf"
        doc.write_bytes(b"%PDF-1.4")
        self.server = Server(str(doc))
        self.lib = FakeLib(self.server)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.replies: dict[str, str] = {}
        self.router = FakeRouter(self.reply, cost=0.002)
        self.store = AiStore(":memory:")
        self.ai = AiService(self.client, self.chats, self.users, self.store,
                            self.router.client(), "m/main", "m/audio", cheap_model="m/cheap",
                            monthly_limit=1.0, translate_to="en")
        await self.push(
            new_chat(CHAT, "Olena", 10), new_chat(GROUP, "Hiking", 20, "chatTypeSupergroup"),
            new_chat(OTHER, "Accounting", 30), new_chat(SECRET, "Secret", 40, "chatTypeSecret"),
            *({"@type": "updateUser", "user": {"id": u, "first_name": name, "last_name": ""}}
              for u, name in ((ME, "Me"), (5, "Olena"), (6, "Petr"), (7, "Jana"), (8, "Karel"))),
            {"@type": "updateOption", "name": "my_id",
             "value": {"@type": "optionValueInteger", "value": str(ME)}},
        )
        for chat in (CHAT, GROUP, OTHER):
            self.ai.set_enabled(chat, True)

    def reply(self, body: dict[str, Any]) -> str:
        system = body["messages"][0]["content"]
        for marker, text in self.replies.items():
            if isinstance(system, str) and marker in system:
                return text
        return "ok"

    async def push(self, *events: dict[str, Any]) -> None:
        seen: list[Any] = []
        off = self.client.on("updateTestMarker", seen.append)
        for event in (*events, {"@type": "updateTestMarker"}):
            self.lib.push(event)
        await wait_until(lambda: bool(seen))
        off()

    async def done(self, chat_id: int, subject: str) -> Any:
        await wait_until(lambda: self.ai.summary(chat_id, subject).state in ("done", "error"))
        return self.ai.summary(chat_id, subject)


class AssistServiceTest(AssistCase):

    async def test_translate_is_cached_and_costed(self) -> None:
        self.replies["Translate"] = "Let's meet on Friday"
        self.ai.translate(CHAT, 3)
        await wait_until(lambda: self.ai.translation(CHAT, 3).state == "done")
        self.assertEqual(self.ai.translation(CHAT, 3).text, "Let's meet on Friday")
        request = self.router.requests[0]
        self.assertEqual(request["model"], "m/cheap")
        self.assertIn("into English", request["messages"][0]["content"])
        self.assertAlmostEqual(self.ai.spent(CHAT), 0.002)
        # the same text again (here: the user's own draft) comes from the cache, for free
        self.assertEqual(await self.ai.translate_text(CHAT, "let's meet on friday", "en"),
                         "Let's meet on Friday")
        self.assertEqual(len(self.router.requests), 1)
        with self.assertRaises(AiUnavailable):
            await self.ai.translate_text(SECRET, "x", "en")

    async def test_everything_comes_in_the_users_language(self) -> None:
        self.ai.set_translate_to("uk")
        self.ai.summarize(CHAT, "day")
        await self.done(CHAT, "")
        self.ai.explain_message(CHAT, 5, "Olena")
        await self.done(CHAT, "explain:5")
        self.ai.find_events(CHAT)
        await self.done(CHAT, "events")
        for request in self.router.requests:
            system = request["messages"][0]["content"]
            self.assertIn("Ukrainian", system)
            self.assertNotIn("{language}", system)
            self.assertNotIn("language most of", system)

    def test_fixed_words_come_localized(self) -> None:
        context = assist.MessageContext("ctx", summary.Source({}, 0, False), 0, ">>> [m1] x")
        for code in assist.LANGUAGES:
            prompts = [
                assist.explain_prompt(context, code)[0]["content"],
                assist.reply_options_prompt(context, code)[0]["content"],
                assist.answers_prompt("c", "q", "l", [], code)[0]["content"],
                assist.digest_prompt("r", "t", "s", code)[0]["content"],
                summary.person_prompt("c", "n", "r", "t", code)[0]["content"],
                summary.prompt("c", "r", "t", code)[0]["content"],
            ]
            for text in prompts:
                self.assertNotIn("{H:", text)
                self.assertNotIn("{language}", text)
            self.assertIn("## " + assist.word(code, "unclear") + "\n", prompts[0])
            self.assertIn("## " + assist.word(code, "who"), prompts[4])
        uk = assist.explain_prompt(context, "uk")[0]["content"]
        self.assertNotIn("## Unclear", uk)
        self.assertIn("| Хто | Відповідь | Джерело |",
                      assist.answers_prompt("c", "q", "l", [], "uk")[0]["content"])
        self.assertEqual(assist.events_markdown([], "uk"), "Домовлених дат і зустрічей не знайдено.")

    def test_copied_heading_explanations_are_cut(self) -> None:
        text = ("## Що незрозуміло — what is worth clarifying before answering\n- дата\n"
                "## Тон (only if it stands out)\nспокійний — без поспіху\n## Суть: one line")
        self.assertEqual(assist.clean_headings(text),
                         "## Що незрозуміло\n- дата\n## Тон\nспокійний — без поспіху\n## Суть")

    async def test_monthly_limit_blocks_requests(self) -> None:
        self.ai.set_limit(0.001)
        self.store.spent[CHAT] = 0.0015
        self.ai.summarize(CHAT, "day")
        result = await self.done(CHAT, "")
        self.assertEqual(result.state, "error")
        self.assertIn("limit", result.error)
        self.assertEqual(self.router.requests, [])
        self.ai.set_limit(0)  # no limit
        self.ai.summarize(CHAT, "day")
        self.assertEqual((await self.done(CHAT, "")).state, "done")

    async def test_ask_uses_search_hits_and_recent_messages(self) -> None:
        async def searcher(query: str, chat_id: int) -> list[Hit]:
            return [Hit(chat_id, 2, NOW - 240, "Petr", "the contract is in the drive", True,
                        False)]

        self.ai.searcher = searcher
        self.replies["answer a question"] = "In the drive [m2], Petr said so [m77]."
        self.ai.ask(CHAT, "where is the contract?")
        result = await self.done(CHAT, "ask")
        self.assertEqual(result.state, "done", result.error)
        self.assertIn("tgc://message/2", result.text)
        self.assertNotIn("m77", result.text)  # made-up citation dropped
        self.assertEqual(result.question, "where is the contract?")
        sent = self.router.requests[0]["messages"][1]["content"]
        self.assertIn("Question: where is the contract?", sent)
        self.assertIn("[m2]", sent)
        self.assertIn("[m5]", sent)  # recent context too
        self.assertAlmostEqual(result.cost, 0.002)

    async def test_events_extracted_with_links(self) -> None:
        self.replies["extract agreed dates"] = json.dumps({"events": [
            {"title": "Dinner", "start": "2026-03-13T18:00", "location": "Lucerna",
             "ref": "m4"}]})
        self.ai.find_events(CHAT)
        result = await self.done(CHAT, "events")
        self.assertEqual(result.state, "done", result.error)
        self.assertEqual(result.data["events"][0]["title"], "Dinner")
        self.assertIn("tgc://message/4", result.text)

    async def test_collect_answers_sends_replies_and_roster(self) -> None:
        self.replies["how each participant answered"] = (
            "Who comes?\n| Person | Answer | Source |\n|---|---|---|\n| Petr | Yes | [m23] |\n"
            "**No answer:** Karel")
        self.ai.collect_answers(GROUP, 21, "who comes on Saturday?")
        result = await self.done(GROUP, "answers:21")
        self.assertEqual(result.state, "done", result.error)
        sent = self.router.requests[0]["messages"][1]["content"]
        self.assertIn("Question: ", sent)
        self.assertIn("who comes on Saturday?", sent)
        self.assertIn("[m23]", sent)
        self.assertNotIn("[m21]", sent)  # the question itself isn't an answer
        self.assertIn("Members: Petr, Jana, Karel", sent)  # without the asker
        self.assertIn("tgc://message/23", result.text)

    async def test_document_question_sends_the_pdf_natively(self) -> None:
        self.ai.ask_document(CHAT, 99, "what is the notice period?", "terms.pdf")
        result = await self.done(CHAT, "doc:99")
        self.assertEqual(result.state, "done", result.error)
        request = self.router.requests[0]
        self.assertEqual(request["plugins"], assist.PDF_PLUGINS)
        part = request["messages"][1]["content"][0]
        self.assertEqual(part["file"]["filename"], "terms.pdf")
        self.assertTrue(part["file"]["file_data"].endswith(
            base64.b64encode(b"%PDF-1.4").decode()))

    async def test_digest_uses_only_flagged_chats_and_moves_since(self) -> None:
        self.ai.digest()
        self.assertIn("No chats in the digest", (await self.done(0, "digest")).error)
        self.assertTrue(self.ai.set_flag(CHAT, "digest", True))
        self.assertFalse(self.ai.set_flag(SECRET, "digest", True))
        self.ai.set_flag(GROUP, "digest", True)
        self.ai.set_enabled(GROUP, False)  # flag stays, but AI off: excluded
        self.replies["digest of several"] = "## Olena\n- meeting on Friday [m2]"
        self.ai.digest()
        result = await self.done(0, "digest")
        self.assertEqual(result.state, "done", result.error)
        sent = self.router.requests[0]["messages"][1]["content"]
        self.assertIn("## Olena", sent)
        self.assertNotIn("## Hiking", sent)
        self.assertIn(f"tgc://message/{CHAT}/", result.text)
        self.assertGreaterEqual(int(self.store.values["digest_since"]), NOW)
        self.assertAlmostEqual(self.ai.spent(0), 0.002)

    async def test_promises_only_from_chats_where_i_wrote(self) -> None:
        self.replies["what the reader promised"] = "## Accounting\n- **Karel** — invoice [m1]"
        self.ai.promises()
        result = await self.done(0, "promises")
        self.assertEqual(result.state, "done", result.error)
        sent = self.router.requests[0]["messages"][1]["content"]
        self.assertIn("## Accounting", sent)
        self.assertIn("(me)", sent)
        self.assertNotIn("## Hiking", sent)  # I wrote nothing there

    async def test_suggest_reply_is_returned_not_sent(self) -> None:
        self.replies["draft the reader's next message"] = "Sounds good, see you!"
        text = await self.ai.suggest_reply(CHAT, "friendly", reply_to=5)
        self.assertEqual(text, "Sounds good, see you!")
        system = self.router.requests[0]["messages"][0]["content"]
        self.assertIn("warm and friendly", system)
        self.assertIn("Reply to:", self.router.requests[0]["messages"][1]["content"])
        self.assertFalse(any(r["@type"] == "sendMessage" for r in self.lib.sent))

    async def test_smart_notifications_need_their_own_flag(self) -> None:
        self.replies["deserves a notification"] = "no"
        self.assertTrue(await self.ai.is_relevant(CHAT, 5))  # flag off: always notify
        self.assertEqual(self.router.requests, [])
        self.ai.set_flag(CHAT, "smart_notify", True)
        self.assertFalse(await self.ai.is_relevant(CHAT, 5))
        self.replies["deserves a notification"] = "yes"
        self.ai.set_flag(CHAT, "smart_notify", False)
        self.ai.set_flag(CHAT, "smart_notify", True)
        with mock.patch.object(self.store, "cache_get", return_value=None):
            self.assertTrue(await self.ai.is_relevant(CHAT, 5))
        self.ai.set_enabled(CHAT, False)  # AI off: the flag alone does nothing
        self.assertFalse(self.ai.flag(CHAT, "smart_notify"))

    async def test_explain_streams_with_context_and_is_cached(self) -> None:
        self.assertEqual(await self.ai.context_size(CHAT, 5), 5)
        self.assertEqual(await self.ai.context_size(SECRET, 5), 0)  # nothing for secret chats
        self.replies["understand one message"] = (
            "## Gist\nOlena confirms the dinner [m4].\n## What they want from you\nNothing.")
        partial: list[str] = []
        self.ai.subscribe(lambda kind, key: kind == "summary" and key == (CHAT, "explain:5")
                          and self.ai.summary(*key).state == "pending"
                          and self.ai.summary(*key).text and partial.append(
                              self.ai.summary(*key).text))
        self.ai.explain_message(CHAT, 5, "Olena")
        result = await self.done(CHAT, "explain:5")
        self.assertEqual(result.state, "done", result.error)
        self.assertGreater(len(partial), 2)  # streamed: the panel grows while it's written
        self.assertIn("tgc://message/4", result.text)
        request = self.router.requests[0]
        self.assertTrue(request["stream"])
        self.assertEqual(request["model"], "m/cheap")
        prompt = request["messages"][1]["content"]
        self.assertIn("Reply chain, oldest first:\n[m3]", prompt)
        self.assertIn(">>> [m5]", prompt)
        self.assertIn("in English", request["messages"][0]["content"])
        self.ai.explain_message(CHAT, 5, "Olena")  # same message, same edit date: free
        await self.done(CHAT, "explain:5")
        self.assertEqual(len(self.router.requests), 1)

    async def test_reply_options_in_chat_language_with_translation(self) -> None:
        self.ai.set_translate_to("ru")
        self.replies["draft replies"] = (
            "ANALYSIS: Ждёт подтверждения встречи\n"
            "### Согласиться\nJasně, v pátek v 18:00!\nTRANSLATION: Да, в пятницу в 18:00!\n"
            "### Уточнить\nKde přesně?\nTRANSLATION: Где именно?")
        self.ai.suggest_replies(CHAT, 5, "Olena")
        result = await self.done(CHAT, "reply:5")
        self.assertEqual(result.state, "done", result.error)
        self.assertEqual(result.data["analysis"], "Ждёт подтверждения встречи")
        self.assertEqual(result.data["options"], [
            {"label": "Согласиться", "text": "Jasně, v pátek v 18:00!",
             "translation": "Да, в пятницу в 18:00!"},
            {"label": "Уточнить", "text": "Kde přesně?", "translation": "Где именно?"}])
        request = self.router.requests[0]
        self.assertEqual(request["model"], "m/main")
        self.assertIn("How the reader writes in this chat:\n- Friday 18:00 at Lucerna?",
                      request["messages"][1]["content"])
        self.ai.suggest_replies(CHAT, 5, "Olena", "another")
        await wait_until(lambda: len(self.router.requests) == 2)
        await self.done(CHAT, "reply:5")
        self.assertIn("Already suggested:\n- Согласиться\n- Уточнить",
                      self.router.requests[1]["messages"][1]["content"])

    def test_parse_half_streamed_options(self) -> None:
        analysis, options = assist.parse_reply_options(
            "ANALYSIS: asks\n### Agree\nSure, see you\nTRANSLATION: Конечно\n### Ask")
        self.assertEqual((analysis, [o["label"] for o in options]), ("asks", ["Agree"]))

    async def test_notifier_asks_before_showing(self) -> None:
        from tgclient.store.notifications import Notifier

        shown: list[Any] = []

        class Sink:
            def show(self, notice: Any) -> None:
                shown.append(notice)

            def withdraw(self, keys: list[str]) -> None:
                pass

        notifier = Notifier(self.client, self.chats, self.users, Sink())
        notifier.smart = lambda chat_id: chat_id == CHAT
        verdicts = {5: False, 4: True}

        async def relevance(chat_id: int, message_id: int) -> bool:
            return verdicts[message_id]

        notifier.relevance = relevance
        for nid, message in ((1, msg(5, "lol")), (2, msg(4, "Olena, can you call me?"))):
            await self.push({"@type": "updateNotificationGroup", "notification_group_id": 3,
                             "chat_id": CHAT, "added_notifications": [{
                                 "@type": "notification", "id": nid, "is_silent": False,
                                 "type": {"@type": "notificationTypeNewMessage",
                                          "message": message, "show_preview": True}}],
                             "removed_notification_ids": []})
        await wait_until(lambda: bool(shown))
        self.assertEqual([n.message_id for n in shown], [4])

    async def test_forget_deletes_results(self) -> None:
        self.ai.summarize(CHAT, "day")
        await self.done(CHAT, "")
        self.ai.set_flag(CHAT, "digest", True)
        self.ai.forget(CHAT)
        self.assertFalse(self.ai.is_enabled(CHAT))
        self.assertEqual(self.ai.summary(CHAT).state, "")
        self.assertFalse(self.ai.flag_set(CHAT, "digest"))


class ControllerTest(AssistCase):
    """AiController on top of the same fake world."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        qt_app()
        from tgclient.prefs import Prefs
        from tgclient.ui.ai_controller import AiController

        self.prefs = Prefs(Path(tempfile.mkdtemp()) / "prefs.json")
        self.controller = AiController(self.ai, self.chats, prefs=self.prefs)
        self.controller.chatId = CHAT

    async def test_export_events_writes_ics(self) -> None:
        self.replies["extract agreed dates"] = json.dumps({"events": [
            {"title": "Dinner", "start": "2026-03-13T18:00", "ref": "m4"}]})
        self.controller.findEvents()
        await self.done(CHAT, "events")
        self.assertTrue(self.controller.hasEvents)
        self.assertIn("$0.0020", self.controller.summaryInfo)
        folder = tempfile.mkdtemp()
        exported: list[str] = []
        self.controller.eventsExported.connect(exported.append)
        with mock.patch("tgclient.ui.ai_controller.QStandardPaths.writableLocation",
                        return_value=folder), \
                mock.patch("tgclient.ui.ai_controller.QDesktopServices.openUrl") as opened:
            self.controller.exportEvents()
        self.assertTrue(exported and exported[0].endswith("Olena - events.ics"))
        self.assertIn("SUMMARY:Dinner", Path(exported[0]).read_text())
        opened.assert_called_once()

    async def test_settings_persist(self) -> None:
        self.controller.setModels("m/big", "m/small", "")
        self.controller.setMonthlyLimit(2.5)
        self.controller.setTranslateTo("cs")
        self.assertEqual((self.ai.summary_model, self.ai.cheap_model, self.ai.transcription_model),
                         ("m/big", "m/small", "m/audio"))
        self.assertEqual(self.prefs.get("ai_monthly_limit"), 2.5)
        self.assertEqual(self.prefs.get("ai_language"), "cs")
        self.controller.setDigest(True)
        self.assertTrue(self.controller.digestEnabled)
        self.assertEqual(self.controller.digestChats, 1)

    async def test_suggest_reply_signal_and_errors(self) -> None:
        self.replies["draft the reader's next message"] = "Sure!"
        suggested: list[str] = []
        self.controller.replySuggested.connect(suggested.append)
        self.controller.suggestReply("brief", 0)
        await wait_until(lambda: bool(suggested))
        self.assertEqual(suggested, ["Sure!"])
        self.controller.chatId = SECRET
        self.controller.translateDraft("hi", "en")
        await wait_until(lambda: self.controller.assistError != "")
        self.assertIn("secret", self.controller.assistError)

    async def test_insert_reply_into_the_composer(self) -> None:
        self.replies["draft replies"] = "ANALYSIS: x\n### Agree\nSure!"
        inserted: list[tuple[str, Any]] = []
        self.controller.insertReply.connect(lambda text, mid: inserted.append((text, mid)))
        self.controller.suggestReplies(5, "Olena")
        await self.done(CHAT, "reply:5")
        self.assertEqual(self.controller.replyOptions[0]["label"], "Agree")
        self.controller.insertOption(0)
        self.assertEqual(inserted, [("Sure!", 5)])
        self.assertFalse(any(r["@type"] == "sendMessage" for r in self.lib.sent))

    async def test_document_panel_and_global_links(self) -> None:
        self.controller.openDocument(99, "terms.pdf")
        self.assertTrue(self.controller.canAsk)
        self.assertEqual(self.controller.summaryName, "terms.pdf")
        self.controller.ask("what is the notice period?")
        await self.done(CHAT, "doc:99")
        self.assertEqual(self.controller.summaryQuestion, "what is the notice period?")
        self.assertEqual(self.controller.parseLink("tgc://message/60/31"), [60, 31])


if __name__ == "__main__":
    unittest.main()
