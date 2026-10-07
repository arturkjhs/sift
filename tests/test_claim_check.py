""""Check what <name> said": the scope (chats with AI on only, never secret), the candidates
and their limits, evidence checked by code (made-up and foreign ids dropped, no evidence ->
not_found, quotes from TDLib, not from the model's JSON), the cache kept in memory only."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest import mock

from fakes import qt_app, wait_until
from test_assist import CHAT, GROUP, OTHER, SECRET, AssistCase

from tgclient.services import ai as ai_module
from tgclient.services import assist
from tgclient.services.person_search import PersonSearch


def verdict(kind: str, *evidence: Any, explanation: str = "She did.") -> str:
    return json.dumps({"verdict": kind, "explanation": explanation, "evidence": list(evidence)})


class ClaimCase(AssistCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ai.person_search = PersonSearch(self.client, self.chats)
        self.answer = verdict("not_found")
        self.router.reply = lambda body: self.answer

    async def check(self, chats: list[int], claim: str = "she said friday") -> Any:
        key = self.ai.check_claim("user:5", "Olena", chats, CHAT, claim)
        assert key is not None
        await wait_until(lambda: self.ai.claim_check(key).state in ("done", "error"))
        return key, self.ai.claim_check(key)


class ClaimCheckTest(ClaimCase):
    async def test_scope(self) -> None:
        self.ai.set_enabled(OTHER, False)
        key, result = await self.check([CHAT, GROUP, OTHER, SECRET])
        self.assertEqual(key[1], (CHAT, GROUP))  # AI off and secret: not checked
        self.assertEqual(result.skipped, 2)
        self.assertEqual(result.chats, ("Olena", "Hiking"))
        sent = self.router.requests[-1]["messages"][1]["content"]
        self.assertIn("Chat: Olena", sent)
        self.assertIn("Chat: Hiking", sent)
        self.assertNotIn("Accounting", sent)

        _, nothing = await self.check([OTHER, SECRET])
        self.assertEqual((nothing.state, nothing.skipped), ("error", 2))
        self.assertEqual(len(self.router.requests), 1)  # nothing sent

    async def test_evidence_is_checked_by_code(self) -> None:
        # m2 is her "let's meet on friday"; m9 doesn't exist; 4 is my message's Telegram id
        # (shown only as [ctx]); the model's own "text" must not be shown
        self.answer = verdict("confirmed", {"id": "m2", "relation": "supports", "text": "FAKE"},
                              {"id": "m9", "relation": "supports"},
                              {"id": 4, "relation": "contradicts"})
        _, result = await self.check([CHAT, GROUP])
        self.assertEqual(result.verdict, "confirmed")
        self.assertEqual([(q.chat_id, q.message_id) for q in result.quotes], [(CHAT, 3)])
        self.assertEqual(result.quotes[0].text, "let's meet on friday")
        self.assertEqual(result.quotes[0].chat_title, "Olena")
        sent = self.router.requests[-1]["messages"][1]["content"]
        lines = sent.splitlines()
        ctx = next(i for i, line in enumerate(lines) if line.startswith("[ctx]"))
        self.assertIn("Friday 18:00 at Lucerna?", lines[ctx])  # the message she replied to
        self.assertTrue(lines[ctx + 1].startswith("[m3]"))
        self.assertNotIn("Lucerna", " ".join(x for x in lines if x.startswith("[m")))
        self.assertEqual((result.count, result.cost), (4, 0.002))
        self.assertIn('"not_found"', self.router.requests[-1]["messages"][0]["content"])

    async def test_no_evidence_is_not_found(self) -> None:
        self.answer = verdict("confirmed", {"id": "m77", "relation": "supports"})
        _, result = await self.check([CHAT])
        self.assertEqual((result.verdict, result.quotes, result.explanation),
                         ("not_found", (), ""))
        self.assertTrue(result.since and result.until >= result.since)
        self.assertEqual(result.count, 3)

    async def test_nothing_of_theirs_costs_nothing(self) -> None:
        key = self.ai.check_claim("user:7", "Jana", [CHAT], CHAT, "anything")
        await wait_until(lambda: self.ai.claim_check(key).state == "done")
        self.assertEqual((self.ai.claim_check(key).verdict, self.router.requests),
                         ("not_found", []))

    async def test_candidate_limits_and_order(self) -> None:
        found = await self.ai.claim_candidates("user:5", [CHAT, GROUP], CHAT, "said friday")
        self.assertEqual(found[0]["id"], 3)  # matches the claim's words: first
        self.assertEqual({m["id"] for m in found}, {1, 3, 5, 21})
        with mock.patch.object(ai_module, "CLAIM_MAX_MESSAGES", 2):
            found = await self.ai.claim_candidates("user:5", [CHAT, GROUP], CHAT, "friday")
            self.assertEqual(len(found), 2)
        with mock.patch.object(ai_module, "CLAIM_MAX_CHARS", 25):
            found = await self.ai.claim_candidates("user:5", [CHAT, GROUP], CHAT, "friday")
            self.assertEqual([m["id"] for m in found], [3])  # 20 chars; the next won't fit

    async def test_cache_in_memory_only(self) -> None:
        self.answer = verdict("partly", {"id": "m2", "relation": "supports"})
        with mock.patch.object(self.store, "cache_put") as cache_put, \
                mock.patch.object(self.store, "save_summary") as save_summary:
            first, _ = await self.check([CHAT], "She said FRIDAY.")
            again, result = await self.check([CHAT], "she said   friday")
            cache_put.assert_not_called()
            save_summary.assert_not_called()
        self.assertEqual(first, again)
        self.assertEqual(first[3], assist.PROMPT_VERSION)
        self.assertEqual(len(self.router.requests), 1)  # the same check: from memory
        self.assertEqual(result.verdict, "partly")
        self.assertEqual(self.store.summaries, {})

    def test_parse(self) -> None:
        parsed = assist.parse_claim('```json\n' + verdict(
            "contradicted", {"id": "m1", "relation": "contradicts"}, {"id": "m1"},
            {"id": "x"}, explanation=" No. ") + "\n```")
        self.assertEqual((parsed.verdict, parsed.explanation, parsed.evidence),
                         ("contradicted", "No.", [(1, "contradicts")]))
        self.assertEqual(assist.parse_claim("garbage").verdict, "not_found")
        self.assertEqual(assist.parse_claim('{"verdict": "maybe"}').verdict, "not_found")
        self.assertEqual(assist.claim_keywords("Олена сказала, що поверне гроші"),
                         ["сказа", "повер", "олена"])  # 3 longest, endings cut


class ClaimModelTest(ClaimCase):
    async def test_model_shows_the_check(self) -> None:
        from tgclient.models.person_messages import PersonMessagesModel

        qt_app()
        model = PersonMessagesModel(self.ai.person_search, self.chats, self.users)
        model.use_ai(self.ai)
        model.open(CHAT, "user:5", "Olena")
        self.answer = verdict("confirmed", {"id": "m2", "relation": "supports"},
                              explanation="Yes, on Friday.")
        model.check("she said friday")
        await wait_until(lambda: model.checkState == "done")
        self.assertEqual((model.verdict, model.explanation), ("confirmed", "Yes, on Friday."))
        self.assertEqual([q["messageId"] for q in model.quotes], [3])
        self.assertEqual(model.quotes[0]["text"], "let&#x27;s meet on friday")
        self.assertEqual((model.checkCost, model.checkModel), ("$0.0020", "m/main"))
        model.open(CHAT, "user:6", "Petr")  # another person: the check is gone
        self.assertEqual(model.checkState, "")


if __name__ == "__main__":
    unittest.main()
