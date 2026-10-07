"""Proxies: adding, switching, pinging, removing; proxy links."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import FakeLib, ok, qt_app, wait_until

from tgclient.store import links
from tgclient.td import TdHub
from tgclient.ui.proxy import proxy_obj


class Server:
    def __init__(self) -> None:
        self.proxies: list[dict[str, Any]] = []

    def __call__(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        extra = req.get("@extra")
        match req["@type"]:
            case "addProxy":
                for added in self.proxies:
                    added["is_enabled"] = False
                added = {"@type": "addedProxy", "id": len(self.proxies) + 1,
                         "is_enabled": req["enable"], "proxy": req["proxy"]}
                self.proxies.append(added)
                return [{**added, "@extra": extra}]
            case "getProxies":
                return [{"@type": "addedProxies", "proxies": self.proxies, "@extra": extra}]
            case "disableProxy":
                for added in self.proxies:
                    added["is_enabled"] = False
            case "removeProxy":
                self.proxies = [p for p in self.proxies if p["id"] != req["proxy_id"]]
            case "pingProxy":
                return [{"@type": "seconds", "seconds": 0.042, "@extra": extra}]
            case "getInternalLinkType":
                return [{"@type": "internalLinkTypeProxy", "@extra": extra,
                         "proxy": proxy_obj("mtproto", "p.example.com", 443, secret="ee00")}]
        return [ok(req)]


class ProxyTest(unittest.IsolatedAsyncioTestCase):
    async def test_proxies(self) -> None:
        qt_app()
        from tgclient.ui.proxy import ProxyController

        lib = FakeLib(Server())
        hub = TdHub(lib)
        self.addCleanup(hub.stop)
        client = hub.create_client()
        proxies = ProxyController(client)
        failed: list[str] = []
        proxies.failed.connect(failed.append)
        proxies.add("socks5", "", 1080, "", "", "")
        self.assertTrue(failed)
        proxies.add("socks5", "127.0.0.1", 1080, "me", "pw", "")
        await wait_until(lambda: len(proxies.list) == 1)
        row = proxies.list[0]
        self.assertEqual((row["title"], row["kind"], row["enabled"]),
                         ("127.0.0.1:1080", "socks5", True))
        self.assertTrue(proxies.active)
        proxies.ping(row["id"])
        await wait_until(lambda: proxies.list[0]["ping"] == "42 ms")
        proxies.setEnabled(row["id"], False)
        await wait_until(lambda: not proxies.active)
        target = await links.resolve(client, "tg://proxy?server=p.example.com&port=443")
        self.assertEqual((target.kind, target.invite["type"]["@type"]),
                         ("proxy", "proxyTypeMtproto"))
        proxies.addFromLink(target.invite)
        await wait_until(lambda: len(proxies.list) == 2 and proxies.list[1]["enabled"])
        proxies.remove(1)
        await wait_until(lambda: len(proxies.list) == 1)


if __name__ == "__main__":
    unittest.main()
