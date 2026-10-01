"""Console client for debugging the td/ layer: log in and print the main chat list."""

from __future__ import annotations

import asyncio
import getpass
import logging
import threading
from typing import Any

from .config import load_settings
from .store.chats import MAIN, ChatStore
from .td import AuthFlow, TdError, TdHub, TdJson, TdlibParams

_CODE_TYPES = {
    "authenticationCodeTypeTelegramMessage": "in Telegram on another device",
    "authenticationCodeTypeSms": "via SMS",
    "authenticationCodeTypeCall": "via phone call",
    "authenticationCodeTypeFlashCall": "via flash call",
    "authenticationCodeTypeFragment": "on Fragment",
}


async def _ainput(prompt: str, secret: bool = False) -> str:
    """Read stdin in a daemon thread so Ctrl+C doesn't hang on a pending input()."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()

    def resolve(value: str | None, error: BaseException | None) -> None:
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(value or "")

    def worker() -> None:
        try:
            value = (getpass.getpass if secret else input)(prompt)
            loop.call_soon_threadsafe(resolve, value, None)
        except BaseException as e:  # noqa: BLE001 — EOFError, KeyboardInterrupt in the thread
            try:
                loop.call_soon_threadsafe(resolve, None, e)
            except RuntimeError:
                pass

    threading.Thread(target=worker, daemon=True).start()
    return await future


class ConsoleAuthUI:
    async def ask_phone(self) -> str:
        return await _ainput("Phone number (international format, e.g. +420...): ")

    async def ask_code(self, code_info: dict[str, Any]) -> str:
        code_type = code_info.get("type", {}).get("@type", "")
        where = _CODE_TYPES.get(code_type, code_type)
        return await _ainput(f"Code sent {where}: ")

    async def ask_password(self, hint: str) -> str:
        suffix = f" (hint: {hint})" if hint else ""
        return await _ainput(f"2FA password{suffix}: ", secret=True)

    async def ask_email(self) -> str:
        return await _ainput("Login email address: ")

    async def ask_email_code(self, code_info: dict[str, Any]) -> str:
        return await _ainput("Code sent to email: ")

    async def show_link(self, link: str) -> None:
        print(f"Confirm login on another device: {link}")

    async def show_error(self, message: str) -> None:
        print(f"Error: {message}")


async def amain() -> None:
    settings = load_settings()
    logging.getLogger().setLevel(settings.log_level)
    settings.database_dir.mkdir(parents=True, exist_ok=True)
    settings.files_dir.mkdir(parents=True, exist_ok=True)

    lib = TdJson()
    lib.execute({"@type": "setLogVerbosityLevel", "new_verbosity_level": settings.td_log_level})

    hub = TdHub(lib)
    client = hub.create_client()
    # Subscribers must exist before the first request, otherwise early updates are lost.
    chats = ChatStore(client)
    auth = AuthFlow(
        client,
        ConsoleAuthUI(),
        TdlibParams(
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            database_dir=settings.database_dir,
            files_dir=settings.files_dir,
            use_test_dc=settings.use_test_dc,
        ),
    )

    try:
        try:
            version = await client.send({"@type": "getOption", "name": "version"})
            print(f"TDLib {version.get('value', '?')}, data in {settings.data_dir}")
        except TdError:
            pass  # the request's only purpose is to start the TDLib instance

        await auth.run()
        me = await client.send({"@type": "getMe"})
        print(f"\nLogged in as {me.get('first_name', '')} {me.get('last_name', '')} (id {me['id']})\n")

        await chats.load_more(MAIN, limit=30)
        for chat in chats.chats_in(MAIN)[:30]:
            unread = f"  [{chat.unread_count}]" if chat.unread_count else ""
            print(f"{chat.type:<10} {chat.title}{unread}")
    finally:
        await client.close()
        hub.stop()


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
