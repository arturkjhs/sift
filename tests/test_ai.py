"""AI features: OpenRouter client, storage, privacy rules, transcription and summaries."""

from __future__ import annotations

import base64
import tempfile
import time
import re
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from fakes import FakeLib, FakeRouter, error, new_chat, ok, qt_app, wait_until

from tgclient.services import summary
from tgclient.services.ai import AiService
from tgclient.services.ai_store import AiStore, StoredSummary
from tgclient.services.openrouter import OpenRouterError
from tgclient.store.chats import ChatStore
from tgclient.store.format import message_stamp
from tgclient.store.markdown import markdown_to_html
from tgclient.store.richtext import Palette
from tgclient.store.users import UserStore
from tgclient.td import TdHub

CHAT = 42
SECRET = 43
NOW = int(time.time())


def msg(mid: int, text: str = "", sender: int = 5, ago: int = 0, **fields: Any) -> dict[str, Any]:
    return {"@type": "message", "id": mid, "chat_id": CHAT, "is_outgoing": False,
            "sender_id": {"@type": "messageSenderUser", "user_id": sender}, "date": NOW - ago,
            "content": {"@type": "messageText", "text": {"text": text or f"m{mid}"}}, **fields}


def _ts(*parts: int) -> int:
    return int(datetime(*parts).timestamp())  # noqa: DTZ001 - local time, as the app shows it


class OpenRouterTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_request_is_pinned_to_zero_retention(self) -> None:
        router = FakeRouter("  hi  ")
        client = router.client()
        reply = await client.complete("m/x", [{"role": "user", "content": "q"}])
        self.assertEqual(reply.text, "hi")
        await client.transcribe("m/y", b"OggS", "ogg", "transcribe")
        await client.aclose()
        for body in router.requests:
            self.assertEqual(body["provider"], {"zdr": True, "data_collection": "deny"})
        self.assertEqual(router.headers[0]["authorization"], "Bearer sk-test")
        audio = router.requests[1]["messages"][0]["content"][1]
        self.assertEqual(audio["type"], "input_audio")
        self.assertEqual(audio["input_audio"]["format"], "ogg")
        self.assertEqual(base64.b64decode(audio["input_audio"]["data"]), b"OggS")

    async def test_errors(self) -> None:
        client = FakeRouter(status=404).client()
        with self.assertRaisesRegex(OpenRouterError, "zero-data-retention"):
            await client.complete("m/x", [])
        client = FakeRouter(status=401).client()
        with self.assertRaisesRegex(OpenRouterError, "API key"):
            await client.complete("m/x", [])


class AiStoreTest(unittest.TestCase):
    def test_persists_across_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "ai.sqlite3"
            store = AiStore(path)
            store.save_enabled(1, True)
            store.save_enabled(2, True)
            store.save_enabled(2, False)
            store.save_transcript(1, 10, "hello", "m")
            store.save_summary((1, ""), StoredSummary("day", "- x", "m", 5))
            store.save_summary((1, "user:5"), StoredSummary("person", "- y", "m", 6, "Olena"))
            store.close()
            again = AiStore(path)
            self.assertEqual(again.enabled, {1})
            self.assertEqual(again.transcripts, {(1, 10): "hello"})
            self.assertEqual(again.summaries[(1, "")].text, "- x")
            self.assertEqual(again.summaries[(1, "user:5")].name, "Olena")
            again.close()


class SummaryHelpersTest(unittest.TestCase):
    def test_linkify_keeps_only_real_ids(self) -> None:
        t3, t5 = _ts(2026, 10, 2, 10, 2), _ts(2026, 10, 2, 10, 5)
        source = summary.Source(times={3: t3, 5: t5}, count=2, truncated=False,
                                senders={3: "Ann"})
        text = summary.linkify("- plan [m3, m99]\n- made up [m77]\n- both [m3; m5]", source)
        self.assertIn(f"- plan[](tgc://message/3?t={t3}&s=Ann)\n", text)
        self.assertNotIn("99", text)
        self.assertNotIn("m77", text)
        self.assertIn("- made up\n", text)  # trailing space left by the dropped citation is gone
        self.assertIn(f"[](tgc://message/5?t={t5})", text)  # no sender known: no &s=
        self.assertEqual(summary.message_id_from_link(f"tgc://message/5?t={t5}&s=Ann"), 5)
        self.assertEqual(summary.message_id_from_link("tgc://message/5"), 5)  # older summaries
        self.assertEqual(summary.message_id_from_link("https://x"), 0)

    def test_at_most_two_links_per_statement(self) -> None:
        source = summary.Source(times={i: 1000 + i for i in range(1, 6)}, count=5,
                                truncated=False)
        text = summary.linkify("a [m1, m2, m3]. b [m4] [m5], [m1]. c [m9, m5]", source)
        self.assertEqual(re.findall(r"message/(\d+)", text), ["1", "2", "4", "5", "5"])
        self.assertIn("a[](tgc://message/1?t=1001)[](tgc://message/2?t=1002). b", text)

    def test_links_are_numbered_in_reading_order(self) -> None:
        minute = _ts(2026, 10, 2, 15, 52)
        source = summary.Source(times={1: minute, 2: minute, 3: minute + 60}, count=3,
                                truncated=False)
        stored = summary.linkify("- a [m2, m1]\n- b [m3; m2]", source)
        shown = summary.number_links(stored)
        self.assertEqual(re.findall(r"\[(\d+)\]\(tgc://message/(\d+)", shown),
                         [("1", "2"), ("2", "1"), ("3", "3"), ("1", "2")])
        self.assertEqual(summary.number_links(shown), shown)  # stable when shown again
        older = f"x [15:52](tgc://message/7?t={minute}) [15:52 #2](tgc://message/8?t={minute})"
        self.assertEqual(summary.number_links(older),
                         f"x [1](tgc://message/7?t={minute}) [2](tgc://message/8?t={minute})")
        self.assertEqual(summary.number_links("[x](tgc://message/7)"), "[x](tgc://message/7)")
        cross = summary.Source(times={1: minute, 2: minute}, count=2, truncated=False,
                               targets={1: "7/10", 2: "8/10"})  # same id, other chats
        self.assertIn("[2](tgc://message/8/10",
                      summary.number_links(summary.linkify("[m1] [m2]", cross)))

    def test_chips_render_small_and_raised(self) -> None:
        html = markdown_to_html("fact[1](tgc://message/7?t=5&s=Ann)", Palette(link="#123456"))
        self.assertIn('href="tgc://message/7?t=5&amp;s=Ann"', html)
        self.assertIn(">¹</span>", html)
        self.assertIn(">¹²</span>", markdown_to_html("[12](tgc://message/7?t=5)", Palette()))
        self.assertIn("color:#123456", html)

    def test_for_you_keeps_only_what_code_marked(self) -> None:
        text = ("## For you\n- Karel asks you to check the build [m5].\n"
                "- Results of ZDP, who applied? [m9]\n\n"
                "**Build.** Broken since Monday [m5, m6].")
        self.assertEqual(summary.keep_for_you(text, "For you", (5,)),
                         "## For you\n- Karel asks you to check the build [m5].\n\n"
                         "**Build.** Broken since Monday [m5, m6].")
        self.assertEqual(summary.keep_for_you(text, "For you", ()),
                         "**Build.** Broken since Monday [m5, m6].")
        self.assertEqual(summary.keep_for_you("## For you:\n- asks all [m9]\n\n**X.** y",
                                              "For you", (5,)), "**X.** y")
        self.assertEqual(summary.keep_for_you("**X.** y [m5]", "For you", (5,)), "**X.** y [m5]")


class MessageStampTest(unittest.TestCase):
    """One label for message times everywhere: links in AI text and lines sent to the model."""

    NOW = datetime(2026, 10, 2, 15, 45)

    def test_today_other_day_other_year(self) -> None:
        self.assertEqual(message_stamp(_ts(2026, 10, 2, 15, 52), self.NOW), "15:52")
        self.assertEqual(message_stamp(_ts(2026, 10, 1, 15, 52), self.NOW), "01.10 15:52")
        self.assertEqual(message_stamp(_ts(2026, 1, 9, 8, 5), self.NOW), "09.01 08:05")
        self.assertEqual(message_stamp(_ts(2025, 10, 1, 15, 52), self.NOW), "01.10.25 15:52")

    def test_midnight(self) -> None:
        just_after = datetime(2026, 10, 2, 0, 1)
        self.assertEqual(message_stamp(_ts(2026, 10, 1, 23, 59), just_after), "01.10 23:59")
        self.assertEqual(message_stamp(_ts(2026, 10, 2, 0, 0), just_after), "00:00")
        new_year = datetime(2026, 1, 1, 0, 1)
        self.assertEqual(message_stamp(_ts(2025, 12, 31, 23, 59), new_year), "31.12.25 23:59")

    def test_link_tooltip_is_sender_and_time_as_of_viewing(self) -> None:
        yesterday = _ts(2026, 10, 1, 15, 52)
        source = summary.Source(times={7: yesterday}, count=1, truncated=False,
                                senders={7: "Eugene Ko (work)"})
        link = re.search(r"\((tgc://[^)]+)\)", summary.linkify("- plan [m7]", source)).group(1)
        self.assertEqual(summary.link_tooltip(link, self.NOW), "Eugene Ko (work), 01.10 15:52")
        self.assertEqual(summary.link_tooltip(link, datetime(2026, 10, 1, 20, 0)),
                         "Eugene Ko (work), 15:52")
        self.assertEqual(summary.link_tooltip(f"tgc://message/7?t={yesterday}", self.NOW),
                         "01.10 15:52")  # older summaries: no sender
        self.assertEqual(summary.link_tooltip("https://x", self.NOW), "")

    def test_model_sees_the_same_format(self) -> None:
        messages = [msg(1, "old", date=_ts(2025, 12, 30, 9, 0)),
                    msg(2, "yesterday", date=_ts(2026, 10, 1, 15, 52)),
                    msg(3, "today", date=_ts(2026, 10, 2, 15, 40))]
        text, _ = summary.render(messages, lambda m: "Ann", lambda m: None, self.NOW)
        self.assertEqual(text.splitlines(), ["[m1] 30.12.25 09:00 Ann: old",
                                             "[m2] 01.10 15:52 Ann: yesterday",
                                             "[m3] 15:40 Ann: today"])
        self.assertTrue(summary.today_header(self.NOW).startswith("Today: 02.10.2026, Friday."))
        prompt = summary.prompt("Team", "Me", text)[1]["content"]
        self.assertTrue(prompt.startswith("Today: "))

    def test_render_includes_transcripts_and_replies(self) -> None:
        voice = {**msg(2), "content": {"@type": "messageVoiceNote", "voice_note": {}}}
        reply = msg(3, "yes", reply_to={"@type": "messageReplyToMessage", "message_id": 2})
        text, source = summary.render(
            [voice, reply], lambda m: "Olena", lambda m: "see you at 5" if m["id"] == 2 else None)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("[m2] "))
        self.assertIn("Olena: Voice message: see you at 5", lines[0])
        self.assertIn("(reply to m2) yes", lines[1])
        self.assertEqual(source.count, 2)


class Server:
    def __init__(self, voice_path: str) -> None:
        self.voice_path = voice_path
        # newest first; odd ids are from user 5, even ids from user 6
        self.history = [msg(i, ago=(10 - i) * 60, sender=5 if i % 2 else 6)
                        for i in range(10, 0, -1)]

    def __call__(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        match req["@type"]:
            case "getMessage":
                if req["message_id"] == 77:
                    return [{**msg(77), "content": {"@type": "messageVoiceNote", "voice_note": {
                        "duration": 2, "voice": _file(500, "")}}, "@extra": req["@extra"]}]
                return [{**msg(req["message_id"]), "@extra": req["@extra"]}]
            case "downloadFile":
                return [{**_file(req["file_id"], self.voice_path), "@extra": req["@extra"]}]
            case "getChatHistory":
                start = req["from_message_id"]
                page = [m for m in self.history if not start or m["id"] < start][:req["limit"]]
                return [{"@type": "messages", "total_count": len(page), "messages": page,
                         "@extra": req["@extra"]}]
            case "searchChatMessages":
                sender = (req.get("sender_id") or {}).get("user_id")
                start = req["from_message_id"]
                mine = [m for m in self.history if m["sender_id"]["user_id"] == sender
                        and (not start or m["id"] < start)][:req["limit"]]
                return [{"@type": "foundChatMessages", "total_count": len(mine), "messages": mine,
                         "next_from_message_id": mine[-1]["id"] if mine else 0,
                         "@extra": req["@extra"]}]
            case "getChat":
                return [error(req, 404, "Not Found")]
        return [ok(req)]


def _file(file_id: int, path: str) -> dict[str, Any]:
    return {"@type": "file", "id": file_id, "size": 4, "expected_size": 4,
            "local": {"path": path, "is_downloading_completed": bool(path)}, "remote": {}}


class AiServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        voice = Path(tmp.name) / "voice.ogg"
        voice.write_bytes(b"OggS")
        self.lib = FakeLib(Server(str(voice)))
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.router = FakeRouter("**Plan.** Ship on Friday [m9, m2].\n\n**Chat.** Mostly logistics [m10].")
        self.store = AiStore(":memory:")
        self.ai = AiService(self.client, self.chats, self.users, self.store,
                            self.router.client(), "sum/model", "voice/model")
        self.events: list[tuple[str, Any]] = []
        self.ai.subscribe(lambda kind, payload: self.events.append((kind, payload)))
        self.lib.push(new_chat(CHAT, "Team", 10, last_read_inbox_message_id=7))
        self.lib.push(new_chat(SECRET, "Secret", 9, "chatTypeSecret"))
        await wait_until(lambda: SECRET in self.chats.chats)

    async def asyncTearDown(self) -> None:
        await self.ai.close()

    async def test_off_by_default_and_never_for_secret_chats(self) -> None:
        self.assertFalse(self.ai.is_enabled(CHAT))
        self.assertFalse(self.ai.set_enabled(SECRET, True))
        self.assertFalse(self.ai.is_enabled(SECRET))
        self.ai.summarize(SECRET, "day")
        self.ai.summarize(CHAT, "day")
        self.ai.transcribe(CHAT, 77)
        self.assertIn("secret", self.ai.summary(SECRET).error)
        self.assertIn("turned off", self.ai.summary(CHAT).error)
        self.assertEqual(self.ai.transcript(CHAT, 77).state, "error")
        self.assertEqual(self.router.requests, [])
        self.assertFalse(any(r["@type"] == "getChatHistory" for r in self.lib.sent))

    async def test_switch_persists(self) -> None:
        self.assertTrue(self.ai.set_enabled(CHAT, True))
        await wait_until(lambda: 1 == next(iter(self.store._db.execute(
            "SELECT ai_enabled FROM chat_settings WHERE chat_id = ?", (CHAT,))), [0])[0])
        self.assertEqual(self.ai.enabled_chats(), [CHAT])
        self.assertIn(("enabled", CHAT), self.events)

    async def test_transcribe_voice(self) -> None:
        self.ai.set_enabled(CHAT, True)
        self.router.reply = "see you at five"
        self.ai.transcribe(CHAT, 77)
        self.assertEqual(self.ai.transcript(CHAT, 77).state, "pending")
        await wait_until(lambda: self.ai.transcript(CHAT, 77).state != "pending")
        self.assertEqual(self.ai.transcript(CHAT, 77).text, "see you at five")
        body = self.router.requests[0]
        self.assertEqual(body["model"], "voice/model")
        audio = body["messages"][0]["content"][1]["input_audio"]
        self.assertEqual(base64.b64decode(audio["data"]), b"OggS")
        await wait_until(lambda: bool(list(self.store._db.execute("SELECT * FROM transcripts"))))
        self.ai.transcribe(CHAT, 77)  # done: no second request
        self.assertEqual(len(self.router.requests), 1)

    async def test_transcribe_rejects_non_voice(self) -> None:
        self.ai.set_enabled(CHAT, True)
        self.ai.transcribe(CHAT, 5)
        await wait_until(lambda: self.ai.transcript(CHAT, 5).state == "error")
        self.assertEqual(self.router.requests, [])

    async def test_summarize_unread_with_links(self) -> None:
        self.ai.set_enabled(CHAT, True)
        self.ai.summarize(CHAT, "unread")
        await wait_until(lambda: self.ai.summary(CHAT).state != "pending")
        result = self.ai.summary(CHAT)
        self.assertEqual(result.state, "done", result.error)
        self.assertEqual(result.count, 3)  # ids 8..10: after last_read_inbox_message_id=7
        self.assertIn("(tgc://message/9?t=", result.text)
        self.assertIn("(tgc://message/10?t=", result.text)
        self.assertNotIn("message/2?", result.text)  # cited, but not part of the input
        body = self.router.requests[0]
        self.assertEqual(body["model"], "sum/model")
        user_text = body["messages"][1]["content"]
        self.assertIn("Chat: Team", user_text)
        self.assertEqual([line.split()[0] for line in user_text.splitlines()
                          if line.startswith("[m")], ["[m8]", "[m9]", "[m10]"])
        await wait_until(lambda: (CHAT, "") in self.store.summaries)

    async def test_person_summary_uses_only_their_messages(self) -> None:
        self.ai.set_enabled(CHAT, True)
        self.router.reply = "## What they know and can help with\n- Backend [m9, m2]"
        self.ai.summarize_person(CHAT, "user:5", "Olena")
        self.assertEqual(self.ai.summary(CHAT, "user:5").state, "pending")
        self.assertEqual(self.ai.summary(CHAT).state, "")  # the chat summary is separate
        await wait_until(lambda: self.ai.summary(CHAT, "user:5").state != "pending")
        result = self.ai.summary(CHAT, "user:5")
        self.assertEqual(result.state, "done", result.error)
        self.assertEqual((result.scope, result.name, result.count), ("person", "Olena", 5))
        self.assertIn("(tgc://message/9?t=", result.text)
        self.assertNotIn("message/2?", result.text)  # not one of hers
        body = self.router.requests[0]
        self.assertIn("profile", body["messages"][0]["content"])
        user_text = body["messages"][1]["content"]
        self.assertIn("Person: Olena", user_text)
        ids = [line.split()[0] for line in user_text.splitlines() if line.startswith("[m")]
        self.assertEqual(ids, ["[m1]", "[m3]", "[m5]", "[m7]", "[m9]"])
        await wait_until(lambda: (CHAT, "user:5") in self.store.summaries)

    async def test_api_key_from_settings_is_checked_saved_and_used(self) -> None:
        qt_app()
        from tgclient.ui.ai_controller import AiController

        saved: list[str] = []
        removed: list[bool] = []
        controller = AiController(self.ai, self.chats, router_factory=self.router.client,
                                  save_key=saved.append, remove_key=lambda: removed.append(True))
        controller.removeApiKey()
        self.assertFalse(controller.property("configured"))
        await wait_until(lambda: removed == [True])

        controller.saveApiKey("  sk-wrong  ")
        await wait_until(lambda: not controller.property("keyBusy"))
        self.assertIn("API key", controller.property("keyError"))
        self.assertEqual(saved, [])
        self.assertFalse(controller.property("configured"))

        good = "sk-or-v1-0123456789abcdef"
        self.router.valid_key = good
        controller.saveApiKey(good)
        await wait_until(lambda: not controller.property("keyBusy"))
        self.assertEqual(controller.property("keyError"), "")
        self.assertEqual(saved, [good])
        self.assertTrue(controller.property("configured"))
        self.assertEqual(controller.property("apiKeyHint"), "sk-or-v1-\u2026cdef")
        self.assertEqual(controller.property("apiKeySource"), "settings")

        self.ai.set_enabled(CHAT, True)  # the new key is used without a restart
        self.ai.summarize(CHAT, "day")
        await wait_until(lambda: self.ai.summary(CHAT).state != "pending")
        self.assertEqual(self.router.headers[-1]["authorization"], f"Bearer {good}")

    async def test_person_summary_follows_the_chat_switch(self) -> None:
        self.ai.summarize_person(CHAT, "user:5", "Olena")
        self.assertIn("turned off", self.ai.summary(CHAT, "user:5").error)
        with self.assertRaises(ValueError):
            self.ai.summarize_person(CHAT, "nobody", "X")
        self.assertEqual(self.router.requests, [])

    async def test_summary_of_nothing(self) -> None:
        self.ai.set_enabled(CHAT, True)
        self.chats.chats[CHAT].last_read_inbox_message_id = 10
        self.ai.summarize(CHAT, "unread")
        await wait_until(lambda: self.ai.summary(CHAT).state == "error")
        self.assertEqual(self.ai.summary(CHAT).error, "No unread messages")
        self.assertEqual(self.router.requests, [])

    async def test_router_error_is_shown(self) -> None:
        self.ai.set_enabled(CHAT, True)
        self.router.status = 404
        self.ai.summarize(CHAT, "day")
        await wait_until(lambda: self.ai.summary(CHAT).state == "error")
        self.assertIn("zero-data-retention", self.ai.summary(CHAT).error)

    async def test_transcript_role_in_message_model(self) -> None:
        qt_app()
        from tgclient.models.messages import MessageListModel, Role

        model = MessageListModel(self.client, self.chats, self.users, self.ai)
        changed: list[Any] = []
        model.dataChanged.connect(lambda a, b, roles: changed.append(list(roles)))
        self.lib._responder = _with_voice_history(self.lib._responder)
        model.open(CHAT)
        await wait_until(lambda: model.rowCount() > 0)
        index = model.index(model.rowOf(77))
        self.assertEqual(model.data(index, Role.TranscriptState), "")
        self.ai.set_enabled(CHAT, True)
        self.router.reply = "hello"
        self.ai.transcribe(CHAT, 77)
        await wait_until(lambda: model.data(index, Role.TranscriptState) == "done")
        self.assertEqual(model.data(index, Role.Transcript), "hello")
        self.assertIn([Role.Transcript, Role.TranscriptState], changed)


def _with_voice_history(responder: Any) -> Any:
    def respond(req: dict[str, Any]) -> list[dict[str, Any]]:
        if req["@type"] == "getChatHistory":
            if req["from_message_id"]:
                return [{"@type": "messages", "total_count": 0, "messages": [],
                         "@extra": req["@extra"]}]
            voice = {**msg(77), "content": {"@type": "messageVoiceNote", "voice_note": {
                "duration": 2, "waveform": "", "voice": _file(500, "")}}}
            return [{"@type": "messages", "total_count": 2, "messages": [voice, msg(76)],
                     "@extra": req["@extra"]}]
        return responder(req)
    return respond


if __name__ == "__main__":
    unittest.main()
