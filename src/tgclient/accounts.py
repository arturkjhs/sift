"""Which Telegram accounts this app has: data_dir/accounts.json. Qt-free.

The first account keeps the original layout (data_dir/prod/...), so existing installs need no
migration; added accounts live in data_dir/accounts/<key>/prod/... Keys are short and URL-safe:
they also prefix image URLs (FileManager.account) and notification keys.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

FIRST = "0"
MAX_ACCOUNTS = 5


@dataclass(frozen=True)
class Account:
    key: str
    folder: str  # relative to data_dir; "" for the first account

    @property
    def is_first(self) -> bool:
        return self.folder == ""


class AccountRegistry:
    def __init__(self, data_dir: Path | None) -> None:
        self._path = data_dir / "accounts.json" if data_dir is not None else None
        self._data_dir = data_dir
        self.accounts: list[Account] = []
        self.active = FIRST
        if self._path is not None and self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self.accounts = [Account(a["key"], a.get("folder", ""))
                                 for a in raw.get("accounts", [])]
                self.active = raw.get("active", FIRST)
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.warning("Ignoring unreadable %s: %s", self._path, e)
        if not self.accounts:
            self.accounts = [Account(FIRST, "")]
        if self.active not in self.keys():
            self.active = self.accounts[0].key

    def keys(self) -> list[str]:
        return [a.key for a in self.accounts]

    def get(self, key: str) -> Account | None:
        return next((a for a in self.accounts if a.key == key), None)

    def add(self) -> Account:
        if len(self.accounts) >= MAX_ACCOUNTS:
            raise ValueError(f"At most {MAX_ACCOUNTS} accounts")
        key = secrets.token_hex(3)
        while key in self.keys() or key == FIRST:
            key = secrets.token_hex(3)
        account = Account(key, f"accounts/{key}")
        self.accounts.append(account)
        self.save()
        return account

    def remove(self, key: str, delete_files: bool = True) -> None:
        """Forget an account (after logging out). The first account's folder is reused by the
        next login, so only added accounts' folders are deleted."""
        account = self.get(key)
        if account is None:
            return
        self.accounts.remove(account)
        if delete_files and self._data_dir is not None and account.folder:
            shutil.rmtree(self._data_dir / account.folder, ignore_errors=True)
        if not self.accounts:
            self.accounts = [Account(FIRST, "")]
        if self.active == key:
            self.active = self.accounts[0].key
        self.save()

    def set_active(self, key: str) -> None:
        if key in self.keys() and key != self.active:
            self.active = key
            self.save()

    def save(self) -> None:
        if self._path is None:
            return
        data = {"accounts": [{"key": a.key, "folder": a.folder} for a in self.accounts],
                "active": self.active}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as e:
            log.warning("Saving accounts failed: %s", e)
