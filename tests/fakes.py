"""Shared fakes for tests: a scripted stand-in for libtdjson."""

from __future__ import annotations

import json
import queue
from collections.abc import Callable
from typing import Any

import httpx

Responder = Callable[[dict[str, Any]], list[dict[str, Any]]]


class FakeLib:
    """Mimics TdJson: send() feeds scripted events into the queue td_receive reads from."""

    def __init__(self, responder: Responder | None = None) -> None:
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._responder = responder or (lambda req: [ok(req)])
        self.sent: list[dict[str, Any]] = []

    def create_client_id(self) -> int:
        return 1

    def send(self, client_id: int, request: dict[str, Any]) -> None:
        self.sent.append(request)
        for event in self._responder(request):
            self.push(event, client_id)

    def push(self, event: dict[str, Any], client_id: int = 1) -> None:
        self._queue.put({**event, "@client_id": client_id})

    def receive(self, timeout: float) -> dict[str, Any] | None:
        try:
            return self._queue.get(timeout=min(timeout, 0.05))
        except queue.Empty:
            return None


class FakeRouter:
    """httpx transport standing in for OpenRouter: records requests, replies from a script."""

    def __init__(self, reply: str = "ok", status: int = 200) -> None:
        self.reply = reply
        self.status = status
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.key_checks: list[str] = []
        self.valid_key = "sk-test"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":  # /key: checking an API key
            self.key_checks.append(request.headers.get("authorization", ""))
            if request.headers.get("authorization") != f"Bearer {self.valid_key}":
                return httpx.Response(401, json={"error": {"message": "No auth credentials"}})
            return httpx.Response(200, json={"data": {"label": "test", "usage": 0}})
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": self.reply}}]})

    def client(self, key: str = "sk-test") -> Any:
        from tgclient.services.openrouter import OpenRouter

        return OpenRouter(key, transport=httpx.MockTransport(self))


class FakeEmbedder:
    """Deterministic stand-in for the local embedding model: words of the same "concept"
    (across languages) share a dimension, any other word gets a hashed one."""

    CONCEPTS: tuple[frozenset[str], ...] = (
        frozenset({"meet", "meeting", "встреча", "встречаемся", "собираемся", "sraz"}),
        frozenset({"keys", "key", "ключи", "ключ", "klíče", "klíč"}),
        frozenset({"ill", "sick", "заболел", "болею", "nemocný"}),
    )
    DIM = 64

    def __init__(self, model: str = "fake/embedder") -> None:
        self.model = model
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def _vector(self, text: str) -> Any:
        import re
        import zlib

        import numpy as np

        vector = np.zeros(self.DIM, dtype=np.float32)
        for word in re.findall(r"\w+", text.lower()):
            concept = next((i for i, c in enumerate(self.CONCEPTS) if word in c), None)
            index = concept if concept is not None else (
                len(self.CONCEPTS) + zlib.crc32(word.encode()) % (self.DIM - len(self.CONCEPTS)))
            vector[index] += 1.0
        norm = float(np.linalg.norm(vector)) or 1.0
        return vector / norm

    def embed_passages(self, texts: list[str]) -> Any:
        import numpy as np

        return np.stack([self._vector(t) for t in texts])

    def embed_query(self, text: str) -> Any:
        return self._vector(text)


def ok(req: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"@type": "ok", **fields, "@extra": req["@extra"]}


def error(req: dict[str, Any], code: int, message: str) -> dict[str, Any]:
    return {"@type": "error", "code": code, "message": message, "@extra": req["@extra"]}


def auth_state(state_type: str, **fields: Any) -> dict[str, Any]:
    return {
        "@type": "updateAuthorizationState",
        "authorization_state": {"@type": state_type, **fields},
    }


def position(order: int, kind: str = "chatListMain", pinned: bool = False, **list_fields: Any):
    return {"@type": "chatPosition", "list": {"@type": kind, **list_fields},
            "order": str(order), "is_pinned": pinned}


def new_chat(chat_id: int, title: str, order: int = 0, kind: str = "chatTypePrivate",
             **fields: Any) -> dict[str, Any]:
    chat_type: dict[str, Any] = {"@type": kind}
    if kind == "chatTypeSupergroup":
        chat_type["is_channel"] = fields.pop("is_channel", False)
    chat = {"id": chat_id, "title": title, "type": chat_type,
            "positions": [position(order)] if order else [], **fields}
    return {"@type": "updateNewChat", "chat": chat}


async def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll until events from the fake receive thread have been dispatched."""
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def qt_app():
    """Single offscreen QGuiApplication shared by all Qt tests."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.instance() or QGuiApplication([])
