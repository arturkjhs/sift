"""Settings → Proxy: SOCKS5, MTProto or HTTP proxies for TDLib (add, switch on/off, ping,
remove), and tg://proxy / t.me/proxy links. Exposed to QML as `proxies`.

TDLib keeps the proxy list in its database, so it survives restarts by itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)


def proxy_obj(kind: str, server: str, port: int, username: str = "", password: str = "",
              secret: str = "") -> dict[str, Any]:
    match kind:
        case "mtproto":
            proxy_type: dict[str, Any] = {"@type": "proxyTypeMtproto", "secret": secret}
        case "http":
            proxy_type = {"@type": "proxyTypeHttp", "username": username, "password": password,
                          "http_only": False}
        case _:
            proxy_type = {"@type": "proxyTypeSocks5", "username": username,
                          "password": password}
    return {"@type": "proxy", "server": server, "port": int(port), "type": proxy_type}


def _kind(proxy: dict[str, Any]) -> str:
    return {"proxyTypeMtproto": "mtproto", "proxyTypeHttp": "http"}.get(
        (proxy.get("type") or {}).get("@type", ""), "socks5")


class ProxyController(QObject):
    changed = Signal()
    failed = Signal(str)

    def __init__(self, client: TdClient, parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._rows: list[dict[str, Any]] = []
        self._raw: dict[int, dict[str, Any]] = {}
        self._pings: dict[int, str] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    @Property("QVariantList", notify=changed)
    def list(self) -> list[dict[str, Any]]:
        """{id, title, kind, enabled, ping}."""
        return [{**row, "ping": self._pings.get(row["id"], "")} for row in self._rows]

    @Property(bool, notify=changed)
    def active(self) -> bool:
        return any(row["enabled"] for row in self._rows)

    @Slot()
    def refresh(self) -> None:
        self._spawn(self._load())

    @Slot(str, str, int, str, str, str)
    def add(self, kind: str, server: str, port: int, username: str, password: str,
            secret: str) -> None:
        if not server.strip() or not 0 < port < 65536:
            self.failed.emit("A server and a port are needed")
            return
        self._spawn(self._run({"@type": "addProxy", "enable": True, "comment": "",
                               "proxy": proxy_obj(kind, server.strip(), port, username,
                                                  password, secret.strip())}))

    @Slot(int, bool)
    def setEnabled(self, proxy_id: int, enabled: bool) -> None:
        self._spawn(self._run({"@type": "enableProxy", "proxy_id": proxy_id} if enabled
                              else {"@type": "disableProxy"}))

    @Slot(int)
    def remove(self, proxy_id: int) -> None:
        self._spawn(self._run({"@type": "removeProxy", "proxy_id": proxy_id}))

    @Slot(int)
    def ping(self, proxy_id: int) -> None:
        self._spawn(self._ping(proxy_id))

    @Slot("QVariantMap")
    def addFromLink(self, proxy: dict[str, Any]) -> None:
        """A proxy link (internalLinkTypeProxy) clicked and confirmed: add it, switched on."""
        self._spawn(self._run({"@type": "addProxy", "enable": True, "comment": "",
                               "proxy": proxy}))

    async def _load(self) -> None:
        try:
            found = await self._client.send({"@type": "getProxies"})
        except TdError as e:
            log.info("getProxies failed: %s", e)
            return
        self._rows, self._raw = [], {}
        for added in found.get("proxies") or []:
            proxy = added.get("proxy") or {}
            self._raw[added["id"]] = proxy
            self._rows.append({"id": added["id"], "kind": _kind(proxy),
                               "title": f"{proxy.get('server', '')}:{proxy.get('port', 0)}",
                               "enabled": bool(added.get("is_enabled"))})
        self.changed.emit()

    async def _run(self, request: dict[str, Any]) -> None:
        try:
            await self._client.send(request)
        except TdError as e:
            log.warning("%s failed: %s", request["@type"], e)
            self.failed.emit(e.message)
        await self._load()

    async def _ping(self, proxy_id: int) -> None:
        proxy = self._raw.get(proxy_id)
        if proxy is None:
            return
        self._pings[proxy_id] = "…"
        self.changed.emit()
        try:
            result = await self._client.send({"@type": "pingProxy", "proxy": proxy}, timeout=20)
            self._pings[proxy_id] = f"{round(float(result.get('seconds', 0)) * 1000)} ms"
        except (TdError, TimeoutError):
            self._pings[proxy_id] = "unreachable"
        self.changed.emit()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
