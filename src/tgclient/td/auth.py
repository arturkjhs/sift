"""Authorization flow driven by TDLib's updateAuthorizationState.

AuthFlow owns the state machine; the UI (console now, QML later) implements AuthUI.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import platform
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .client import Event, TdClient, TdError

log = logging.getLogger(__name__)

# ask_phone() may answer this instead of a number: log in by scanning a QR code with a phone
# where the account is already signed in (requestQrCodeAuthentication).
QR_LOGIN = "\x00qr"

# How long to wait after a rejected input for a possible state change before re-prompting
# (e.g. too many wrong codes moves TDLib back to WaitPhoneNumber).
_STATE_SETTLE_DELAY = 0.2


class AuthError(RuntimeError):
    """Authorization cannot continue (account not registered, client closed, ...)."""


class AuthUI(Protocol):
    async def ask_phone(self) -> str: ...
    async def ask_code(self, code_info: dict[str, Any]) -> str: ...
    async def ask_password(self, hint: str) -> str: ...
    async def ask_email(self) -> str: ...
    async def ask_email_code(self, code_info: dict[str, Any]) -> str: ...
    async def show_link(self, link: str) -> None: ...
    async def show_error(self, message: str) -> None: ...


@dataclass(frozen=True)
class TdlibParams:
    api_id: int
    api_hash: str
    database_dir: Path
    files_dir: Path
    database_encryption_key: bytes = b""  # from the Vault (Session), b"" before encryption
    use_test_dc: bool = False
    system_language_code: str = "en"
    device_model: str = field(default_factory=lambda: f"{platform.system()} {platform.machine()}")
    system_version: str = field(default_factory=platform.platform)
    application_version: str = "0.1.0"

    def to_request(self) -> dict[str, Any]:
        return {
            "@type": "setTdlibParameters",
            "use_test_dc": self.use_test_dc,
            "database_directory": str(self.database_dir),
            "files_directory": str(self.files_dir),
            "database_encryption_key": base64.b64encode(self.database_encryption_key).decode(),
            "use_file_database": True,
            "use_chat_info_database": True,
            "use_message_database": True,
            "use_secret_chats": True,
            "api_id": self.api_id,
            "api_hash": self.api_hash,
            "system_language_code": self.system_language_code,
            "device_model": self.device_model,
            "system_version": self.system_version,
            "application_version": self.application_version,
        }


class AuthFlow:
    """Create before the client's first request so no authorization state is missed."""

    def __init__(self, client: TdClient, ui: AuthUI, params: TdlibParams) -> None:
        self._client = client
        self._ui = ui
        self.params = params  # may be replaced before run() (the database key)
        self._states: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._unsubscribe = client.on("updateAuthorizationState", self._on_state)

    def _on_state(self, event: Event) -> None:
        self._states.put_nowait(event["authorization_state"])

    async def run(self) -> None:
        """Returns when authorized (authorizationStateReady)."""
        try:
            while True:
                state = await self._states.get()
                while not self._states.empty():  # act only on the latest state
                    state = self._states.get_nowait()

                state_type = state["@type"]
                log.debug("Authorization state: %s", state_type)
                if state_type == "authorizationStateReady":
                    return
                await self._handle(state)
        finally:
            self._unsubscribe()

    async def _handle(self, state: dict[str, Any]) -> None:
        ui = self._ui
        match state["@type"]:
            case "authorizationStateWaitTdlibParameters":
                try:
                    await self._client.send(self.params.to_request())
                except TdError as e:
                    raise AuthError(f"Can't open the local database: {e.message}") from e

            case "authorizationStateWaitPhoneNumber":
                await self._prompt_and_send(
                    ui.ask_phone,
                    lambda v: {"@type": "requestQrCodeAuthentication", "other_user_ids": []}
                    if v == QR_LOGIN
                    else {"@type": "setAuthenticationPhoneNumber", "phone_number": v},
                )

            case "authorizationStateWaitEmailAddress":
                await self._prompt_and_send(
                    ui.ask_email,
                    lambda v: {"@type": "setAuthenticationEmailAddress", "email_address": v},
                )

            case "authorizationStateWaitEmailCode":
                info = state.get("code_info", {})
                await self._prompt_and_send(
                    lambda: ui.ask_email_code(info),
                    lambda v: {
                        "@type": "checkAuthenticationEmailCode",
                        "code": {"@type": "emailAddressAuthenticationCode", "code": v},
                    },
                )

            case "authorizationStateWaitCode":
                info = state.get("code_info", {})
                await self._prompt_and_send(
                    lambda: ui.ask_code(info),
                    lambda v: {"@type": "checkAuthenticationCode", "code": v},
                )

            case "authorizationStateWaitPassword":
                hint = state.get("password_hint", "")
                await self._prompt_and_send(
                    lambda: ui.ask_password(hint),
                    lambda v: {"@type": "checkAuthenticationPassword", "password": v},
                    strip=False,
                )

            case "authorizationStateWaitOtherDeviceConfirmation":
                # tg://login?token=...: shown as a QR code; TDLib sends a fresh link when the
                # token expires, and the next state once the phone confirms.
                await ui.show_link(state.get("link", ""))

            case "authorizationStateWaitRegistration":
                raise AuthError(
                    "This phone number has no Telegram account. Register in an official app first."
                )

            case "authorizationStateLoggingOut" | "authorizationStateClosing" | (
                "authorizationStateClosed"
            ):
                raise AuthError(f"Client is shutting down ({state['@type']})")

            case other:
                log.warning("Unhandled authorization state: %s", other)

    async def _prompt_and_send(
        self,
        ask: Callable[[], Awaitable[str]],
        build: Callable[[str], dict[str, Any]],
        strip: bool = True,
    ) -> None:
        while True:
            value = await ask()
            if strip:
                value = value.strip()
            if not value:
                continue
            try:
                await self._client.send(build(value))
                return
            except TdError as e:
                await self._ui.show_error(e.message)
                await asyncio.sleep(_STATE_SETTLE_DELAY)
                if not self._states.empty():
                    return  # TDLib moved to another state; let run() handle it
