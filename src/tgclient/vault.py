"""Keys that protect the data on disk. Qt-free.

- Each account's TDLib database is encrypted with its own random 32-byte key
  (`database_encryption_key`). The key lives in the system keyring (Keychain, Secret Service);
  where there is none, in `vault.json` next to the data (owner-only file: protects nothing
  against someone with the user's files, but keeps the database portable).
- With a passcode, the keys are kept only wrapped by it (scrypt → AES-GCM) in `vault.json`
  and removed from the keyring: the app must be unlocked before TDLib can start, and the AI
  data (results, search index) is stored sealed with a key derived from the database key.

A database created before encryption has the empty key: TDLib opens it with b"", and
`Session` re-encrypts it once logged in (setDatabaseEncryptionKey), then stores the new key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

SERVICE = "tgclient"
KEY_BYTES = 32
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}
_CHECK = b"tgclient passcode check"


class Keyring(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


class VaultLocked(RuntimeError):
    """A key is needed but the passcode hasn't been entered."""


def system_keyring() -> Keyring | None:
    """The OS keyring, or None (no backend, or TGC_KEY_STORE=file)."""
    if os.environ.get("TGC_KEY_STORE", "").lower() == "file":
        return None
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring) or "null" in type(backend).__module__.lower():
            return None
        return keyring  # type: ignore[return-value]
    except Exception as e:  # noqa: BLE001 - any backend problem means "no keyring"
        log.info("No system keyring: %s", e)
        return None


def seal(data: bytes, key: bytes) -> bytes:
    """AES-GCM with a random nonce: nonce + ciphertext (with tag)."""
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, data, None)


def unseal(blob: bytes, key: bytes) -> bytes:
    """Raises ValueError for a wrong key or damaged data."""
    try:
        return AESGCM(key).decrypt(blob[:12], blob[12:], None)
    except (InvalidTag, ValueError) as e:
        raise ValueError("Can't decrypt: wrong key or damaged data") from e


def derive(key: bytes, purpose: str) -> bytes:
    """A separate key for another use of the same secret (HMAC-SHA256 as a KDF)."""
    return hmac.new(key, purpose.encode(), hashlib.sha256).digest()


class Vault:
    def __init__(self, data_dir: Path | None, keyring: Keyring | None = None) -> None:
        self._path = data_dir / "vault.json" if data_dir is not None else None
        self._keyring = keyring
        self._state: dict[str, Any] = {"version": 1, "passcode": None, "keys": {}}
        self._master: bytes | None = None  # passcode-derived, while unlocked
        if self._path is not None and self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._state.update(loaded)
            except (OSError, ValueError) as e:
                log.error("Unreadable %s: %s", self._path, e)

    # --- passcode ---------------------------------------------------------------------------

    @property
    def has_passcode(self) -> bool:
        return bool(self._state.get("passcode"))

    @property
    def locked(self) -> bool:
        return self.has_passcode and self._master is None

    def unlock(self, passcode: str) -> bool:
        info = self._state.get("passcode")
        if not info:
            return True
        master = _stretch(passcode, base64.b64decode(info["salt"]))
        if not hmac.compare_digest(derive(master, "check"), base64.b64decode(info["check"])):
            return False
        self._master = master
        return True

    def check(self, passcode: str) -> bool:
        """The passcode is right (unlocking the window while the app runs)."""
        info = self._state.get("passcode")
        if not info:
            return True
        master = _stretch(passcode, base64.b64decode(info["salt"]))
        return hmac.compare_digest(derive(master, "check"), base64.b64decode(info["check"]))

    def set_passcode(self, passcode: str) -> None:
        """Set or change (must be unlocked): keys move out of the keyring into vault.json,
        wrapped by the new passcode."""
        if self.locked:
            raise VaultLocked("Unlock first")
        keys = {account: self._read_key(account) for account in self._state["keys"]}
        salt = secrets.token_bytes(16)
        self._master = _stretch(passcode, salt)
        self._state["passcode"] = {"salt": _b64(salt),
                                   "check": _b64(derive(self._master, "check"))}
        for account, key in keys.items():
            if key is not None:
                self._store_key(account, key)
        self._save()

    def remove_passcode(self) -> None:
        """Keys go back to the keyring (or the plain file). Must be unlocked."""
        if self.locked:
            raise VaultLocked("Unlock first")
        keys = {account: self._read_key(account) for account in self._state["keys"]}
        self._state["passcode"] = None
        self._master = None
        for account, key in keys.items():
            if key is not None:
                self._store_key(account, key)
        self._save()

    # --- database keys ----------------------------------------------------------------------

    def database_key(self, account: str) -> bytes | None:
        """The account's stored key; None if it has none yet. Raises VaultLocked."""
        if account not in self._state["keys"]:
            return None
        if self.locked:
            raise VaultLocked("The passcode is needed")
        return self._read_key(account)

    def new_key(self) -> bytes:
        return secrets.token_bytes(KEY_BYTES)

    def store_database_key(self, account: str, key: bytes) -> None:
        if self.locked:
            raise VaultLocked("The passcode is needed")
        self._store_key(account, key)
        self._save()

    def forget(self, account: str) -> None:
        """The account logged out: its key goes too."""
        entry = self._state["keys"].pop(account, None)
        if entry and entry.get("store") == "keyring" and self._keyring is not None:
            try:
                self._keyring.delete_password(SERVICE, _username(account))
            except Exception as e:  # noqa: BLE001
                log.info("Removing a key from the keyring failed: %s", e)
        self._save()

    def data_key(self, account: str) -> bytes | None:
        """Key for the account's sealed AI data: only with a passcode (else data is plain)."""
        if not self.has_passcode:
            return None
        key = self.database_key(account)
        return derive(key, "ai-data") if key else None

    # --- internals --------------------------------------------------------------------------

    def _read_key(self, account: str) -> bytes | None:
        entry = self._state["keys"].get(account) or {}
        match entry.get("store"):
            case "wrapped":
                if self._master is None:
                    raise VaultLocked("The passcode is needed")
                return unseal(base64.b64decode(entry["key"]), derive(self._master, account))
            case "keyring":
                if self._keyring is None:
                    log.error("The key of %s is in a keyring that isn't available", account)
                    return None
                value = self._keyring.get_password(SERVICE, _username(account))
                return base64.b64decode(value) if value else None
            case "file":
                return base64.b64decode(entry["key"])
        return None

    def _store_key(self, account: str, key: bytes) -> None:
        previous = (self._state["keys"].get(account) or {}).get("store")
        if self._master is not None and self.has_passcode:
            entry = {"store": "wrapped",
                     "key": _b64(seal(key, derive(self._master, account)))}
        elif self._keyring is not None and self._keyring_set(account, key):
            entry = {"store": "keyring"}
        else:
            entry = {"store": "file", "key": _b64(key)}
        if previous == "keyring" and entry["store"] != "keyring" and self._keyring is not None:
            try:
                self._keyring.delete_password(SERVICE, _username(account))
            except Exception as e:  # noqa: BLE001
                log.info("Removing a key from the keyring failed: %s", e)
        self._state["keys"][account] = entry

    def _keyring_set(self, account: str, key: bytes) -> bool:
        try:
            assert self._keyring is not None
            self._keyring.set_password(SERVICE, _username(account), _b64(key))
            return True
        except Exception as e:  # noqa: BLE001 - denied, locked, no backend
            log.warning("Can't use the system keyring, keeping the key in a file: %s", e)
            return False

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.touch(mode=0o600, exist_ok=True)
        tmp.chmod(0o600)
        tmp.write_text(json.dumps(self._state, indent=1), encoding="utf-8")
        tmp.replace(self._path)


def _stretch(passcode: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passcode.encode(), salt=salt, dklen=KEY_BYTES, maxmem=64 * 2**20,
                          **_SCRYPT)


def _username(account: str) -> str:
    return f"database-key:{account}"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def database_exists(database_dir: Path) -> bool:
    """TDLib created its database already (opened so far with the empty key)."""
    return (database_dir / "td.binlog").exists() or (database_dir / "td_test.binlog").exists()


class SealedFile:
    """One of our SQLite databases kept encrypted on disk (`<name>.sealed`) while a passcode is
    set; the database itself then lives in memory (AiStore/SearchIndex `initial`, `snapshot`).
    Without a key it is the plain file, as before."""

    def __init__(self, path: Path, key: bytes | None) -> None:
        self.path = path
        self.sealed = path.with_name(path.name + ".sealed")
        self.key = key

    def load(self) -> bytes | None:
        """The database image to start from: the sealed file, or the plain one being sealed
        for the first time. None: start empty (or use the plain file directly, without key)."""
        if self.key is None:
            return None
        if self.sealed.exists():
            try:
                return unseal(self.sealed.read_bytes(), self.key)
            except (OSError, ValueError) as e:
                log.error("Can't open %s, starting empty: %s", self.sealed, e)
                return None
        if self.path.exists():
            import sqlite3

            plain = sqlite3.connect(str(self.path))
            try:
                return plain.serialize()
            finally:
                plain.close()
        return None

    def save(self, data: bytes, final: bool = False) -> None:
        """Write the image: sealed with a key, else plain. `final` (closing): the plain copy
        a passcode replaces goes (not earlier: SQLite may still be writing to it)."""
        if self.key is not None:
            _write_private(self.sealed, seal(data, self.key))
            if final:
                for leftover in self.path.parent.glob(self.path.name + "*"):
                    if leftover != self.sealed and not leftover.name.endswith(".tmp"):
                        leftover.unlink(missing_ok=True)
        else:
            _write_private(self.path, data)
            self.sealed.unlink(missing_ok=True)


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.touch(mode=0o600, exist_ok=True)
    tmp.write_bytes(data)
    tmp.replace(path)
