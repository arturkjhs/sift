"""Settings → Privacy (who sees what of me), notifications by chat type, and storage
(TDLib's cache: size, clearing it). Exposed to QML as `privacy`."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..store.media import human_size
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

# Settings shown, in order: key -> TDLib userPrivacySetting.
SETTINGS = {
    "lastSeen": "userPrivacySettingShowStatus",
    "photo": "userPrivacySettingShowProfilePhoto",
    "phone": "userPrivacySettingShowPhoneNumber",
    "findByPhone": "userPrivacySettingAllowFindingByPhoneNumber",
    "forwards": "userPrivacySettingShowLinkInForwardedMessages",
    "invites": "userPrivacySettingAllowChatInvites",
    "calls": "userPrivacySettingAllowCalls",
    "bio": "userPrivacySettingShowBio",
}
# The main rule of a setting, as the picker shows it; exceptions (users, chats) are kept.
_MAIN = {"userPrivacySettingRuleAllowAll": "everybody",
         "userPrivacySettingRuleAllowContacts": "contacts",
         "userPrivacySettingRuleRestrictAll": "nobody"}
_RULE = {value: key for key, value in _MAIN.items()}
SCOPES = {"private": "notificationSettingsScopePrivateChats",
          "groups": "notificationSettingsScopeGroupChats",
          "channels": "notificationSettingsScopeChannelChats"}
MUTED = 366 * 86400


def main_rule(rules: list[dict[str, Any]]) -> str:
    """everybody | contacts | nobody (TDLib applies rules in order; the broad one decides)."""
    for rule in rules:
        if rule.get("@type") in _MAIN:
            return _MAIN[rule["@type"]]
    return "nobody"


def with_main_rule(rules: list[dict[str, Any]], choice: str) -> list[dict[str, Any]]:
    """The rules with the broad one replaced (exceptions for users and chats kept, before it,
    as TDLib wants them)."""
    exceptions = [r for r in rules if r.get("@type") not in _MAIN
                  and r.get("@type") != "userPrivacySettingRuleRestrictContacts"]
    rules = [*exceptions, {"@type": _RULE[choice]}]
    if choice == "contacts":
        rules.append({"@type": "userPrivacySettingRuleRestrictAll"})
    return rules


class PrivacyController(QObject):
    changed = Signal()

    def __init__(self, client: TdClient, own_files: list[Path] | None = None,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._own_files = own_files or []  # our databases (AI, search), counted in storage
        self._rules: dict[str, list[dict[str, Any]]] = {}
        self._scopes: dict[str, dict[str, Any]] = {}
        self._storage: dict[str, Any] = {}
        self._busy = False
        self._tasks: set[asyncio.Task[Any]] = set()

    # --- privacy ----------------------------------------------------------------------------

    @Property("QVariantMap", notify=changed)
    def choices(self) -> dict[str, str]:
        """key -> everybody | contacts | nobody, once loaded."""
        return {key: main_rule(rules) for key, rules in self._rules.items()}

    @Slot()
    def load(self) -> None:
        self._spawn(self._load())

    @Slot(str, str)
    def choose(self, key: str, choice: str) -> None:
        if key in SETTINGS and choice in _RULE:
            self._spawn(self._set_rule(key, choice))

    # --- notifications by chat type ---------------------------------------------------------

    @Property("QVariantMap", notify=changed)
    def scopes(self) -> dict[str, bool]:
        """private | groups | channels -> notifications on."""
        return {key: (s.get("mute_for", 0) == 0) for key, s in self._scopes.items()}

    @Slot(str, bool)
    def setScope(self, key: str, on: bool) -> None:
        if key in self._scopes:
            self._spawn(self._set_scope(key, on))

    # --- storage ----------------------------------------------------------------------------

    @Property("QVariantMap", notify=changed)
    def storage(self) -> dict[str, Any]:
        return dict(self._storage)

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Slot()
    def clearCache(self) -> None:
        """Downloaded media and files (TDLib fetches them again when needed)."""
        self._spawn(self._clear())

    # --- internals --------------------------------------------------------------------------

    async def _load(self) -> None:
        for key, setting in SETTINGS.items():
            try:
                found = await self._client.send({"@type": "getUserPrivacySettingRules",
                                                 "setting": {"@type": setting}})
                self._rules[key] = list(found.get("rules") or [])
            except TdError as e:
                log.info("%s: %s", setting, e)
        for key, scope in SCOPES.items():
            try:
                self._scopes[key] = await self._client.send({
                    "@type": "getScopeNotificationSettings", "scope": {"@type": scope}})
            except TdError as e:
                log.info("%s: %s", scope, e)
        await self._load_storage()
        self.changed.emit()

    async def _set_rule(self, key: str, choice: str) -> None:
        rules = with_main_rule(self._rules.get(key, []), choice)
        try:
            await self._client.send({"@type": "setUserPrivacySettingRules",
                                     "setting": {"@type": SETTINGS[key]},
                                     "rules": {"@type": "userPrivacySettingRules",
                                               "rules": rules}})
        except TdError as e:
            log.warning("Privacy %s: %s", key, e)
            return
        self._rules[key] = rules
        self.changed.emit()

    async def _set_scope(self, key: str, on: bool) -> None:
        settings = {**self._scopes[key], "@type": "scopeNotificationSettings",
                    "mute_for": 0 if on else MUTED}
        try:
            await self._client.send({"@type": "setScopeNotificationSettings",
                                     "scope": {"@type": SCOPES[key]},
                                     "notification_settings": settings})
        except TdError as e:
            log.warning("Notifications for %s: %s", key, e)
            return
        self._scopes[key] = settings
        self.changed.emit()

    async def _load_storage(self) -> None:
        try:
            fast = await self._client.send({"@type": "getStorageStatisticsFast"})
        except TdError as e:
            log.info("getStorageStatisticsFast failed: %s", e)
            fast = {}
        own = sum(p.stat().st_size for path in self._own_files
                  for p in path.parent.glob(path.name + "*") if p.is_file())
        self._storage = {
            "files": human_size(int(fast.get("files_size") or 0)),
            "fileCount": int(fast.get("file_count") or 0),
            "database": human_size(int(fast.get("database_size") or 0)),
            "ai": human_size(own),
        }

    async def _clear(self) -> None:
        self._busy = True
        self.changed.emit()
        try:
            await self._client.send({
                "@type": "optimizeStorage", "size": 0, "ttl": 0, "count": 0,
                "immunity_delay": 0, "file_types": [], "chat_ids": [], "exclude_chat_ids": [],
                "return_deleted_file_statistics": False, "chat_limit": 0}, timeout=120)
        except (TdError, TimeoutError) as e:
            log.warning("Clearing the cache failed: %s", e)
        self._busy = False
        await self._load_storage()
        self.changed.emit()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
