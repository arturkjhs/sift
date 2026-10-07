"""Telegram links inside the client: public chats, invite links (join), user links, hashtags,
message links; the join bar for chats opened without being a member."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from fakes import FakeLib, error, new_chat, ok, qt_app, wait_until

from tgclient.store import links
from tgclient.store.chats import ChatStore
from tgclient.store.presence import PresenceStore
from tgclient.store.users import UserStore
from tgclient.td import TdHub

INVITE = {"@type": "chatInviteLinkInfo", "chat_id": 0, "accessible_for": 0, "title": "Prague IT",
          "description": "Meetups", "member_count": 230, "member_user_ids": [],
          "type": {"@type": "inviteLinkChatTypeSupergroup"}, "creates_join_request": False,
          "is_public": False}


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    extra = req.get("@extra")
    match req["@type"]:
        case "getInternalLinkType":
            link = req["link"]
            if "/+" in link or "joinchat" in link:
                return [{"@type": "internalLinkTypeChatInvite", "invite_link": link,
                         "@extra": extra}]
            if link.endswith("/olena") or "domain=olena" in link:
                return [{"@type": "internalLinkTypePublicChat", "chat_username": "olena",
                         "draft_text": "", "open_profile": False, "@extra": extra}]
            if link.endswith("/news"):
                return [{"@type": "internalLinkTypePublicChat", "chat_username": "news",
                         "draft_text": "", "open_profile": False, "@extra": extra}]
            if "/c/" in link or link.endswith("/7"):
                return [{"@type": "internalLinkTypeMessage", "url": link, "@extra": extra}]
            return [error(req, 400, "Not an internal link")]
        case "searchPublicChat":
            if req["username"] == "news":
                return [new_chat(90, "News", 0, "chatTypeSupergroup", is_channel=True,
                                 supergroup_id=900),
                        {"@type": "updateSupergroup", "supergroup": {
                            "id": 900, "is_channel": True, "member_count": 10,
                            "status": {"@type": "chatMemberStatusLeft"}}},
                        {"@type": "chat", "id": 90, "@extra": extra}]
            return [{"@type": "chat", "id": 5, "@extra": extra}]
        case "checkChatInviteLink":
            return [{**INVITE, "@extra": extra}]
        case "joinChatByInviteLink":
            return [new_chat(77, "Prague IT", 3, "chatTypeSupergroup"),
                    {"@type": "chatJoinResultSuccess", "chat_id": 77, "@extra": extra}]
        case "joinChat":
            return [{"@type": "updateSupergroup", "supergroup": {
                "id": 900, "is_channel": True, "member_count": 11,
                "status": {"@type": "chatMemberStatusMember", "member_until_date": 0}}},
                {"@type": "chatJoinResultSuccess", "chat_id": req["chat_id"], "@extra": extra}]
        case "createPrivateChat":
            return [{"@type": "chat", "id": req["user_id"], "@extra": extra}]
        case "getMessageLinkInfo":
            return [{"@type": "messageLinkInfo", "is_public": True, "chat_id": 5,
                     "message": {"id": 7}, "@extra": extra}]
        case "getChatHistory":
            return [{"@type": "messages", "total_count": 0, "messages": [], "@extra": extra}]
    return [ok(req)]


class LinksTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        qt_app()
        self.lib = FakeLib(responder)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        self.presence = PresenceStore(self.client)

    async def test_resolve(self) -> None:
        resolve = lambda link: links.resolve(self.client, link)  # noqa: E731
        self.assertEqual((await resolve("https://t.me/olena")).chat_id, 5)
        self.assertEqual((await resolve("tg://resolve?domain=olena")).chat_id, 5)
        target = await resolve("https://t.me/c/123/7")
        self.assertEqual((target.kind, target.chat_id, target.message_id), ("chat", 5, 7))
        target = await resolve("https://t.me/+AbCdEf")
        self.assertEqual((target.kind, target.invite["title"]), ("invite", "Prague IT"))
        self.assertEqual((await resolve("tg://user?id=42")).chat_id, 42)
        target = await resolve("tg://search?q=%23meetup")
        self.assertEqual((target.kind, target.query), ("search", "#meetup"))
        self.assertEqual((await resolve("https://example.com")).kind, "external")
        self.assertEqual(await links.join(self.client, "https://t.me/+AbCdEf"), 77)

    async def test_model_join_bar_and_signals(self) -> None:
        from tgclient.models.messages import MessageListModel

        model = MessageListModel(self.client, self.chats, self.users, None, self.presence)
        resolved: list[tuple[Any, Any]] = []
        invites: list[dict[str, Any]] = []
        searches: list[str] = []
        model.linkResolved.connect(lambda chat, message: resolved.append((chat, message)))
        model.inviteReady.connect(invites.append)
        model.searchRequested.connect(searches.append)
        with mock.patch("tgclient.models.messages.QDesktopServices") as desktop:
            model.openLink("https://t.me/news")
            await wait_until(lambda: bool(resolved))
            model.openLink("https://t.me/+AbCdEf")
            await wait_until(lambda: bool(invites))
            model.openLink("tg://search?q=%23meetup")
            await wait_until(lambda: bool(searches))
            model.openLink("https://example.com/x")
            self.assertTrue(desktop.openUrl.called)
        self.assertEqual(resolved, [(90, 0)])
        self.assertEqual((invites[0]["members"], invites[0]["channel"]), (230, False))
        self.assertEqual(searches, ["#meetup"])

        model.open(90)  # a channel opened from a link: join, not write
        self.assertTrue(model.canJoin)
        self.assertFalse(model.canWrite)
        model.joinChat()
        await wait_until(lambda: not model.canJoin)
        self.assertFalse(model.canWrite)  # a channel subscriber still can't post

        model.joinByInvite(invites[0]["link"])
        await wait_until(lambda: (77, 0) in resolved)


if __name__ == "__main__":
    unittest.main()
