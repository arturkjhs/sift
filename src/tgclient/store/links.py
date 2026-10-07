"""Telegram links (t.me/…, telegram.me, tg:…) resolved to something inside the client. Qt-free.

Messages open the chat at the message, public usernames and phone numbers open the chat,
invite links either open the chat (already a member, or a public chat) or offer to join,
hashtags and cashtags search. Anything else goes to the browser.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

TELEGRAM_LINK = re.compile(r"^(?:tg:|(?:https?://)?(?:www\.)?(?:t\.me|telegram\.(?:me|dog))/)",
                           re.IGNORECASE)


@dataclass
class Target:
    kind: str  # chat | search | invite | external
    chat_id: int = 0
    message_id: int = 0
    query: str = ""
    invite: dict[str, Any] = field(default_factory=dict)  # chatInviteLinkInfo + "link"


async def resolve(client: TdClient, link: str) -> Target:
    if not TELEGRAM_LINK.match(link):
        return Target("external")
    parts = urlsplit(link)
    params = {k: v[0] for k, v in parse_qs(parts.query).items()}
    if link.lower().startswith("tg://search"):
        return Target("search", query=unquote(params.get("q", "")))
    if link.lower().startswith("tg://user") and params.get("id", "").isdigit():
        return await _private_chat(client, int(params["id"]))
    try:
        kind = await client.send({"@type": "getInternalLinkType", "link": link})
    except TdError as e:
        log.info("Not an internal link (%s): %s", e.message, link)
        kind = {}
    try:
        match kind.get("@type"):
            case "internalLinkTypePublicChat":
                chat = await client.send({"@type": "searchPublicChat",
                                          "username": kind["chat_username"]})
                return Target("chat", chat_id=chat["id"])
            case "internalLinkTypeBotStart" | "internalLinkTypeBotStartInGroup":
                chat = await client.send({"@type": "searchPublicChat",
                                          "username": kind["bot_username"]})
                return Target("chat", chat_id=chat["id"])
            case "internalLinkTypeUserPhoneNumber":
                user = await client.send({"@type": "searchUserByPhoneNumber",
                                          "phone_number": kind["phone_number"],
                                          "only_local": False})
                return await _private_chat(client, user["id"])
            case "internalLinkTypeChatInvite":
                invite = kind["invite_link"]
                info = await client.send({"@type": "checkChatInviteLink", "invite_link": invite})
                if info.get("chat_id"):  # set when the chat is readable: member or public
                    return Target("chat", chat_id=info["chat_id"])
                return Target("invite", invite={**info, "link": invite})
            case "internalLinkTypeSavedMessages":
                me = await client.send({"@type": "getMe"})
                return await _private_chat(client, me["id"])
    except (TdError, KeyError) as e:
        log.info("Resolving %s failed: %s", link, e)
        return Target("external")
    # Message links (and anything TDLib didn't classify): ask for the message.
    try:
        info = await client.send({"@type": "getMessageLinkInfo", "url": link})
    except TdError as e:
        log.info("Not a message link (%s): %s", e.message, link)
        return Target("external")
    if info.get("chat_id"):
        return Target("chat", chat_id=info["chat_id"],
                      message_id=(info.get("message") or {}).get("id") or 0)
    return Target("external")


async def join(client: TdClient, invite_link: str = "", chat_id: int = 0) -> int:
    """Join by an invite link or a public chat's id. Returns the chat id (0: a request was
    sent to the admins, or it failed)."""
    try:
        if invite_link:
            result = await client.send({"@type": "joinChatByInviteLink",
                                        "invite_link": invite_link})
        else:
            result = await client.send({"@type": "joinChat", "chat_id": chat_id})
    except TdError as e:
        log.warning("Joining failed: %s", e)
        return 0
    if result.get("@type") == "chatJoinResultSuccess":
        return int(result.get("chat_id") or chat_id)
    if result.get("@type") == "ok":  # older TDLib
        return chat_id
    return 0


async def _private_chat(client: TdClient, user_id: int) -> Target:
    chat = await client.send({"@type": "createPrivateChat", "user_id": user_id, "force": False})
    return Target("chat", chat_id=chat["id"])
