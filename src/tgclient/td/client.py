"""Async TDLib client.

TdHub owns the single td_receive loop (TDLib requires one receiver per process) and routes
events to TdClient instances by @client_id, which keeps multi-account support open.
TdClient matches responses to requests via @extra and dispatches updates to handlers.
All handlers run on the asyncio event loop thread.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import threading
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)

Event = dict[str, Any]
Handler = Callable[[Event], Awaitable[None] | None]


class TdLib(Protocol):
    """What TdHub needs from the native binding. TdJson implements it; tests use a fake."""

    def create_client_id(self) -> int: ...
    def send(self, client_id: int, request: dict[str, Any]) -> None: ...
    def receive(self, timeout: float) -> dict[str, Any] | None: ...


class TdError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class TdHub:
    def __init__(self, lib: TdLib, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._lib = lib
        self._loop = loop or asyncio.get_running_loop()
        self._clients: dict[int, TdClient] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def create_client(self) -> TdClient:
        client_id = self._lib.create_client_id()
        client = TdClient(self, client_id)
        self._clients[client_id] = client
        self._ensure_started()
        return client

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _ensure_started(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._receive_loop, name="td-receive", daemon=True
            )
            self._thread.start()

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            event = self._lib.receive(1.0)
            if event is None:
                continue
            client = self._clients.get(event.pop("@client_id", None))
            if client is None:
                continue  # event for a closed/unknown client
            try:
                self._loop.call_soon_threadsafe(client._dispatch, event)
            except RuntimeError:
                break  # event loop is closed, shutting down

    def _send(self, client_id: int, request: dict[str, Any]) -> None:
        self._lib.send(client_id, request)

    def _forget(self, client_id: int) -> None:
        self._clients.pop(client_id, None)


class TdClient:
    """One TDLib instance (one account). Create via TdHub.create_client().

    Note: TDLib starts emitting updates only after the first request is sent, so register
    handlers (stores, AuthFlow) before the first send().
    """

    def __init__(self, hub: TdHub, client_id: int) -> None:
        self._hub = hub
        self.client_id = client_id
        self._pending: dict[str, asyncio.Future[Event]] = {}
        self._handlers: defaultdict[str, list[Handler]] = defaultdict(list)
        self._extra = itertools.count(1)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = asyncio.Event()

    def on(self, update_type: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to an update type ("*" for all). Returns an unsubscribe function."""
        self._handlers[update_type].append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(update_type, [])
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def send(self, request: dict[str, Any], timeout: float | None = None) -> Event:
        """Send a request and await its response. Raises TdError on {"@type": "error"}."""
        extra = str(next(self._extra))
        future: asyncio.Future[Event] = asyncio.get_running_loop().create_future()
        self._pending[extra] = future
        self._hub._send(self.client_id, {**request, "@extra": extra})
        try:
            result = await asyncio.wait_for(future, timeout) if timeout else await future
        finally:
            self._pending.pop(extra, None)
        if result.get("@type") == "error":
            raise TdError(result.get("code", 0), result.get("message", ""))
        return result

    async def close(self, timeout: float = 10.0) -> None:
        if self._closed.is_set():
            return
        try:
            await self.send({"@type": "close"}, timeout=timeout)
        except (TdError, TimeoutError):
            pass
        try:
            await asyncio.wait_for(self._closed.wait(), timeout)
        except TimeoutError:
            log.warning("TDLib client %s did not close in %.0fs", self.client_id, timeout)

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    def _dispatch(self, event: Event) -> None:
        extra = event.pop("@extra", None)
        if extra is not None:
            future = self._pending.get(str(extra))
            if future is not None and not future.done():
                future.set_result(event)
            return

        update_type = event.get("@type", "")
        if (
            update_type == "updateAuthorizationState"
            and event["authorization_state"]["@type"] == "authorizationStateClosed"
        ):
            self._closed.set()
            self._hub._forget(self.client_id)

        for handler in (*self._handlers.get(update_type, ()), *self._handlers.get("*", ())):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    self._spawn(result)
            except Exception:
                log.exception("Handler %r failed on %s", handler, update_type)

    def _spawn(self, awaitable: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("Async handler failed", exc_info=task.exception())
