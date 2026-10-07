"""Vault: database keys in a keyring or a file, the passcode wrapping them, sealed data."""

from __future__ import annotations

import asyncio
import base64
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from fakes import FakeLib, auth_state, new_chat, ok, qt_app, wait_until

from tgclient.accounts import FIRST
from tgclient.config import Settings
from tgclient.vault import Vault, VaultLocked, seal, unseal


class MemoryKeyring:
    def __init__(self, fail: bool = False) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.fail = fail

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail:
            raise RuntimeError("denied")
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class VaultTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())

    def test_keyring_then_passcode_then_back(self) -> None:
        ring = MemoryKeyring()
        vault = Vault(self.dir, ring)
        self.assertIsNone(vault.database_key(FIRST))
        key = vault.new_key()
        vault.store_database_key(FIRST, key)
        self.assertEqual(len(ring.values), 1)
        self.assertNotIn(key.hex(), (self.dir / "vault.json").read_text())
        self.assertEqual(Vault(self.dir, ring).database_key(FIRST), key)
        self.assertIsNone(vault.data_key(FIRST))  # no passcode: AI data stays plain

        vault.set_passcode("1234")
        self.assertEqual(ring.values, {})  # moved out of the keyring
        reopened = Vault(self.dir, ring)
        self.assertTrue(reopened.locked)
        with self.assertRaises(VaultLocked):
            reopened.database_key(FIRST)
        self.assertFalse(reopened.unlock("0000"))
        self.assertTrue(reopened.unlock("1234"))
        self.assertEqual(reopened.database_key(FIRST), key)
        self.assertEqual(len(reopened.data_key(FIRST) or b""), 32)
        self.assertTrue(reopened.check("1234") and not reopened.check("12345"))
        self.assertEqual(oct((self.dir / "vault.json").stat().st_mode & 0o777), "0o600")

        reopened.set_passcode("5678")  # change
        third = Vault(self.dir, ring)
        self.assertFalse(third.unlock("1234"))
        self.assertTrue(third.unlock("5678"))
        third.remove_passcode()
        self.assertEqual(len(ring.values), 1)
        fourth = Vault(self.dir, ring)
        self.assertFalse(fourth.locked)
        self.assertEqual(fourth.database_key(FIRST), key)
        fourth.forget(FIRST)
        self.assertEqual(ring.values, {})
        self.assertIsNone(Vault(self.dir, ring).database_key(FIRST))

    def test_without_a_keyring_the_key_is_in_the_file(self) -> None:
        vault = Vault(self.dir, MemoryKeyring(fail=True))
        key = vault.new_key()
        vault.store_database_key(FIRST, key)
        self.assertEqual(Vault(self.dir, None).database_key(FIRST), key)

    def test_seal(self) -> None:
        key = Vault(None).new_key()
        blob = seal(b"secret data", key)
        self.assertNotIn(b"secret", blob)
        self.assertEqual(unseal(blob, key), b"secret data")
        with self.assertRaises(ValueError):
            unseal(blob, Vault(None).new_key())


class Server:
    """TDLib asking for parameters, then ready; records the keys it was given."""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.rekeyed: list[str] = []

    def __call__(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        match req["@type"]:
            case "getOption":
                return [auth_state("authorizationStateWaitTdlibParameters"),
                        {"@type": "optionValueString", "value": "x", "@extra": req["@extra"]}]
            case "setTdlibParameters":
                self.keys.append(req["database_encryption_key"])
                return [auth_state("authorizationStateReady"), new_chat(1, "Olena", 5), ok(req)]
            case "setDatabaseEncryptionKey":
                self.rekeyed.append(req["new_encryption_key"])
            case "loadChats":
                return [{"@type": "error", "code": 404, "message": "Not Found",
                         "@extra": req["@extra"]}]
            case "close":
                return [ok(req), auth_state("authorizationStateClosed")]
        return [ok(req)]


class SessionEncryptionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        qt_app()
        self.data = Path(tempfile.mkdtemp())
        self.settings = Settings(api_id=1, api_hash="x", data_dir=self.data, use_test_dc=False,
                                 td_log_level=0, log_level="WARNING")
        self.ring = MemoryKeyring()

    def session(self, vault: Vault, server: Server) -> Any:
        from tgclient.app import Session

        return Session(self.settings, lib=FakeLib(server), vault=vault)

    async def test_new_database_gets_a_key_old_one_is_reencrypted(self) -> None:
        server = Server()
        vault = Vault(self.data, self.ring)
        session = self.session(vault, server)
        await session.start()
        key = vault.database_key(FIRST)
        self.assertEqual(server.keys, [base64.b64encode(key).decode()])
        self.assertEqual(server.rekeyed, [])
        await session.close()

        # a database from before encryption: opened with the empty key, then re-encrypted
        vault.forget(FIRST)
        self.settings.database_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.database_dir / "td.binlog").write_bytes(b"x")
        server = Server()
        session = self.session(vault, server)
        await session.start()
        self.assertEqual(server.keys, [""])
        new = vault.database_key(FIRST)
        self.assertEqual(server.rekeyed, [base64.b64encode(new).decode()])
        await session.close()

    async def test_passcode_seals_ai_data(self) -> None:
        vault = Vault(self.data, self.ring)
        vault.store_database_key(FIRST, vault.new_key())
        vault.set_passcode("1234")
        session = self.session(vault, Server())
        await session.start()
        await wait_until(lambda: 1 in session.chats.chats)
        self.assertTrue(session.ai_service.set_enabled(1, True))
        await asyncio.sleep(0.05)
        await session.close()
        self.assertFalse(self.settings.ai_db_path.exists())
        sealed = self.settings.ai_db_path.with_name("ai.sqlite3.sealed")
        self.assertTrue(sealed.exists())
        self.assertNotIn(b"chat_settings", sealed.read_bytes())

        reopened = Vault(self.data, self.ring)
        self.assertTrue(reopened.unlock("1234"))
        session = self.session(reopened, Server())
        self.assertEqual(session._stores[0].enabled, {1})  # read back from the sealed file

        # the passcode goes: the data is written plain again when the app closes
        from tgclient.ui.lock import LockController

        lock = LockController(reopened, None, sessions=lambda: [session])
        self.assertEqual(lock.removePasscode("0000"), "The passcode is wrong")
        self.assertEqual(lock.removePasscode("1234"), "")
        await session.close()
        self.assertTrue(self.settings.ai_db_path.exists())
        self.assertFalse(sealed.exists())


class LockControllerTest(unittest.TestCase):
    def setUp(self) -> None:
        qt_app()

    def test_set_lock_unlock_auto_lock(self) -> None:
        from tgclient.prefs import Prefs
        from tgclient.ui.lock import LockController

        vault = Vault(Path(tempfile.mkdtemp()), MemoryKeyring())
        prefs = Prefs(None)
        lock = LockController(vault, prefs)
        self.assertFalse(lock.hasPasscode)
        lock.lockNow()
        self.assertFalse(lock.locked)  # nothing to lock with
        self.assertEqual(lock.setPasscode("12", ""), "At least 4 characters")
        self.assertEqual(lock.setPasscode("1234", ""), "")
        self.assertEqual(lock.setPasscode("5678", "0000"), "The current passcode is wrong")
        lock.lockNow()
        self.assertTrue(lock.locked and lock.is_private())
        self.assertFalse(lock.unlock("0000"))
        self.assertTrue(lock.unlock("1234"))
        self.assertFalse(lock.locked)

        lock.setAutoLock(5)
        self.assertEqual(prefs.get("auto_lock_minutes"), 5)
        with mock.patch("tgclient.ui.lock.time.monotonic", return_value=time.monotonic() + 301):
            lock._check_idle()
        self.assertTrue(lock.locked)


if __name__ == "__main__":
    unittest.main()
