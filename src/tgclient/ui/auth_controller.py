"""Bridges AuthFlow (AuthUI protocol) to QML.

Each ask_* call switches `step` and waits until QML calls submit(). TDLib rejections come back
through show_error(), and AuthFlow asks again.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..td.auth import QR_LOGIN

_CODE_DESTINATIONS = {
    "authenticationCodeTypeTelegramMessage": "We sent the code to your Telegram app on another device.",
    "authenticationCodeTypeSms": "We sent the code by SMS.",
    "authenticationCodeTypeCall": "You will receive the code in a phone call.",
    "authenticationCodeTypeFlashCall": "You will receive a call; the code is the caller's number.",
    "authenticationCodeTypeFragment": "The code was sent to Fragment.",
}


class AuthController(QObject):
    """step: loading | phone | code | password | email | emailCode | qr | ready | failed"""

    changed = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._step = "loading"
        self._hint = ""
        self._error = ""
        self._link = ""
        self._busy = False
        self._pending: asyncio.Future[str] | None = None

    # --- QML properties ---------------------------------------------------------------------

    @Property(str, notify=changed)
    def step(self) -> str:
        return self._step

    @Property(str, notify=changed)
    def hint(self) -> str:
        return self._hint

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property(str, notify=changed)
    def link(self) -> str:
        return self._link

    @Property(str, notify=changed)
    def qrSource(self) -> str:
        return f"image://qr/{quote(self._link, safe='')}" if self._link else ""

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Slot()
    def requestQr(self) -> None:
        """On the phone step: log in by scanning a QR code instead."""
        if self._step == "phone":
            self.submit(QR_LOGIN)

    @Slot(str)
    def submit(self, value: str) -> None:
        if self._pending is None or self._pending.done():
            return
        self._busy = True
        self._error = ""
        self.changed.emit()
        self._pending.set_result(value)

    # --- called from Python -----------------------------------------------------------------

    def set_ready(self) -> None:
        self._set("ready", hint="")

    def set_failed(self, message: str) -> None:
        self._busy = False
        self._error = message
        self._set("failed", hint="")

    # --- AuthUI protocol --------------------------------------------------------------------

    async def ask_phone(self) -> str:
        return await self._ask("phone", "Enter your phone number in international format.")

    async def ask_code(self, code_info: dict[str, Any]) -> str:
        code_type = code_info.get("type", {}).get("@type", "")
        return await self._ask("code", _CODE_DESTINATIONS.get(code_type, "Enter the login code."))

    async def ask_password(self, hint: str) -> str:
        text = "Your account is protected with a password."
        return await self._ask("password", f"{text} Hint: {hint}" if hint else text)

    async def ask_email(self) -> str:
        return await self._ask("email", "Enter the email address for login codes.")

    async def ask_email_code(self, code_info: dict[str, Any]) -> str:
        return await self._ask("emailCode", "Enter the code we sent to your email.")

    async def show_link(self, link: str) -> None:
        self._busy = False
        self._link = link
        self._set("qr", hint="")

    async def show_error(self, message: str) -> None:
        # Stay busy: AuthFlow re-asks (or TDLib changes state) right after, and _ask() unlocks
        # input. Unlocking here would let the user submit while nothing is waiting for it.
        self._error = _humanize(message)
        self.changed.emit()

    # --- internals --------------------------------------------------------------------------

    async def _ask(self, step: str, hint: str) -> str:
        self._pending = asyncio.get_running_loop().create_future()
        self._busy = False
        self._set(step, hint=hint)
        try:
            return await self._pending
        finally:
            self._pending = None

    def _set(self, step: str, hint: str) -> None:
        if step != self._step:
            self._error = ""
        self._step = step
        self._hint = hint
        self.changed.emit()


def _humanize(message: str) -> str:
    known = {
        "PHONE_NUMBER_INVALID": "This phone number is not valid. Include the country code, e.g. +420.",
        "PHONE_CODE_INVALID": "Wrong code. Check it and try again.",
        "PHONE_CODE_EXPIRED": "The code has expired. Request a new one.",
        "PASSWORD_HASH_INVALID": "Wrong password.",
        "PHONE_NUMBER_FLOOD": "Too many attempts. Try again later.",
        "PHONE_NUMBER_BANNED": "This phone number is banned from Telegram.",
    }
    if message in known:
        return known[message]
    if message.startswith(("Too Many Requests", "FLOOD_WAIT")):
        return "Too many attempts. Wait a while and try again."
    return message
