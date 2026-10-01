"""Tests for the td/ layer against a fake libtdjson (no network, no native library)."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import Any

from fakes import FakeLib, Responder, auth_state, error, ok

from tgclient.store.chats import ChatStore
from tgclient.td import AuthError, AuthFlow, TdError, TdHub, TdlibParams


class ScriptedUI:
    def __init__(self, phones: list[str], codes: list[str], passwords: list[str]) -> None:
        self.phones, self.codes, self.passwords = phones, codes, passwords
        self.errors: list[str] = []

    async def ask_phone(self) -> str:
        return self.phones.pop(0)

    async def ask_code(self, code_info: dict[str, Any]) -> str:
        return self.codes.pop(0)

    async def ask_password(self, hint: str) -> str:
        return self.passwords.pop(0)

    async def ask_email(self) -> str:
        raise AssertionError("unexpected")

    async def ask_email_code(self, code_info: dict[str, Any]) -> str:
        raise AssertionError("unexpected")

    async def show_link(self, link: str) -> None:
        raise AssertionError("unexpected")

    async def show_error(self, message: str) -> None:
        self.errors.append(message)


PARAMS = TdlibParams(api_id=1, api_hash="x", database_dir=Path("/tmp/db"), files_dir=Path("/tmp/f"))


class TdClientTest(unittest.IsolatedAsyncioTestCase):
    def make(self, responder: Responder) -> tuple[FakeLib, TdHub]:
        lib = FakeLib(responder)
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        return lib, hub

    async def test_response_is_matched_by_extra(self) -> None:
        def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
            return [{"@type": "optionValueString", "value": "1.8.0", "@extra": req["@extra"]}]

        _, hub = self.make(responder)
        client = hub.create_client()
        result = await client.send({"@type": "getOption", "name": "version"}, timeout=2)
        self.assertEqual(result["value"], "1.8.0")
        self.assertNotIn("@extra", result)

    async def test_error_raises_tderror(self) -> None:
        _, hub = self.make(lambda req: [error(req, 400, "BAD_REQUEST")])
        client = hub.create_client()
        with self.assertRaises(TdError) as ctx:
            await client.send({"@type": "whatever"}, timeout=2)
        self.assertEqual(ctx.exception.code, 400)

    async def test_sync_and_async_handlers_receive_updates(self) -> None:
        lib, hub = self.make(lambda req: [])
        client = hub.create_client()
        got_sync: list[int] = []
        got_async = asyncio.Event()

        async def async_handler(event: dict[str, Any]) -> None:
            got_async.set()

        client.on("updateFoo", lambda e: got_sync.append(e["x"]))
        client.on("updateFoo", async_handler)
        lib.push({"@type": "updateFoo", "x": 42})

        await asyncio.wait_for(got_async.wait(), 2)
        self.assertEqual(got_sync, [42])

    async def test_unsubscribe(self) -> None:
        lib, hub = self.make(lambda req: [])
        client = hub.create_client()
        seen: list[dict[str, Any]] = []
        unsubscribe = client.on("*", seen.append)
        unsubscribe()
        lib.push({"@type": "updateFoo"})
        await asyncio.sleep(0.2)
        self.assertEqual(seen, [])


class AuthFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_login_with_wrong_code_and_2fa(self) -> None:
        def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
            match req["@type"]:
                case "getOption":
                    return [
                        auth_state("authorizationStateWaitTdlibParameters"),
                        {"@type": "optionValueString", "value": "1.8.0", "@extra": req["@extra"]},
                    ]
                case "setTdlibParameters":
                    return [auth_state("authorizationStateWaitPhoneNumber"), ok(req)]
                case "setAuthenticationPhoneNumber":
                    info = {"type": {"@type": "authenticationCodeTypeTelegramMessage"}}
                    return [auth_state("authorizationStateWaitCode", code_info=info), ok(req)]
                case "checkAuthenticationCode":
                    if req["code"] != "12345":
                        return [error(req, 400, "PHONE_CODE_INVALID")]
                    return [auth_state("authorizationStateWaitPassword", password_hint="pet"), ok(req)]
                case "checkAuthenticationPassword":
                    if req["password"] != " s3cret ":
                        return [error(req, 400, "PASSWORD_HASH_INVALID")]
                    return [
                        auth_state("authorizationStateReady"),
                        {"@type": "updateNewChat", "chat": {
                            "id": 7, "title": "Friends", "unread_count": 3,
                            "type": {"@type": "chatTypeSupergroup", "is_channel": False},
                        }},
                        ok(req),
                    ]
                case "close":
                    return [ok(req), auth_state("authorizationStateClosed")]
            return [error(req, 400, f"unexpected {req['@type']}")]

        lib = FakeLib(responder)
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        client = hub.create_client()
        chats = ChatStore(client)
        ui = ScriptedUI(phones=[" +420123456789 "], codes=["00000", "12345"], passwords=[" s3cret "])
        flow = AuthFlow(client, ui, PARAMS)

        await client.send({"@type": "getOption", "name": "version"}, timeout=2)
        await asyncio.wait_for(flow.run(), 5)

        sent = [(r["@type"], r) for r in lib.sent]
        types = [t for t, _ in sent]
        self.assertEqual(
            types,
            [
                "getOption",
                "setTdlibParameters",
                "setAuthenticationPhoneNumber",
                "checkAuthenticationCode",
                "checkAuthenticationCode",
                "checkAuthenticationPassword",
            ],
        )
        self.assertEqual(sent[2][1]["phone_number"], "+420123456789")  # stripped
        self.assertEqual(sent[5][1]["password"], " s3cret ")  # passwords are not stripped
        self.assertEqual(ui.errors, ["PHONE_CODE_INVALID"])
        self.assertEqual(chats.chats[7].title, "Friends")
        self.assertEqual(chats.chats[7].type, "supergroup")

        await client.close(timeout=2)
        self.assertTrue(client.is_closed)

    async def test_unregistered_number_raises(self) -> None:
        def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
            if req["@type"] == "getOption":
                return [auth_state("authorizationStateWaitRegistration"), ok(req)]
            return [ok(req)]

        hub = TdHub(FakeLib(responder))
        self.addCleanup(hub.stop)
        client = hub.create_client()
        flow = AuthFlow(client, ScriptedUI([], [], []), PARAMS)
        await client.send({"@type": "getOption", "name": "version"}, timeout=2)
        with self.assertRaises(AuthError):
            await asyncio.wait_for(flow.run(), 5)


if __name__ == "__main__":
    unittest.main()
