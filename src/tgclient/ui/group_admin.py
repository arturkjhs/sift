"""Groups and channels: creating them, the invite link, admins, removing members, editing
the name and description, deleting. Exposed to QML as `groupAdmin`."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication

from ..store.chats import ChatStore
from ..store.presence import PresenceStore
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

# What a new admin may do (Telegram's defaults for a promoted member, minus promoting others).
ADMIN_RIGHTS: dict[str, Any] = {
    "@type": "chatAdministratorRights", "can_manage_chat": True, "can_change_info": True,
    "can_post_messages": True, "can_edit_messages": True, "can_delete_messages": True,
    "can_invite_users": True, "can_restrict_members": True, "can_pin_messages": True,
    "can_manage_topics": True, "can_promote_members": False, "can_manage_video_chats": True,
    "can_post_stories": False, "can_edit_stories": False, "can_delete_stories": False,
    "can_manage_direct_messages": False, "can_manage_tags": False,
    "can_send_welcome_messages": False, "is_anonymous": False,
}


class GroupAdmin(QObject):
    changed = Signal()
    created = Signal("QVariant")  # a new group or channel: open it
    failed = Signal(str)

    def __init__(self, client: TdClient, chats: ChatStore, presence: PresenceStore,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._chats = chats
        self._presence = presence
        self._links: dict[int, str] = {}  # chat id -> primary invite link
        self._revision = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        presence.subscribe(lambda kind, payload: self._bump() if kind == "members" else None)
        self.changed.connect(self._count)

    @Property(int, notify=changed)
    def revision(self) -> int:
        """Bumps on every change: QML bindings calling role()/inviteLink() depend on it."""
        return self._revision

    def _count(self) -> None:
        self._revision += 1

    def _bump(self) -> None:
        self.changed.emit()

    # --- my role ----------------------------------------------------------------------------

    @Slot("QVariant", result=str)
    def role(self, chat_id: Any) -> str:
        """owner | admin | member | "" (not a group, or not known)."""
        chat = self._chats.chats.get(int(chat_id or 0))
        if chat is None or chat.type not in ("group", "supergroup", "channel"):
            return ""
        kind = self._presence.member_status(chat)
        return {"chatMemberStatusCreator": "owner",
                "chatMemberStatusAdministrator": "admin",
                "chatMemberStatusMember": "member",
                "chatMemberStatusRestricted": "member"}.get(kind, "")

    # --- creating ---------------------------------------------------------------------------

    @Slot(str, str, str, "QVariantList")
    def create(self, kind: str, title: str, description: str, user_ids: list[Any]) -> None:
        """kind: group | channel."""
        self._spawn(self._create(kind, title.strip(), description.strip(),
                                 [int(u) for u in user_ids]))

    async def _create(self, kind: str, title: str, description: str,
                      user_ids: list[int]) -> None:
        if not title:
            self.failed.emit("A name is needed")
            return
        try:
            if kind == "channel":
                chat = await self._client.send({
                    "@type": "createNewSupergroupChat", "title": title, "is_forum": False,
                    "is_channel": True, "description": description, "location": None,
                    "message_auto_delete_time": 0, "for_import": False})
                chat_id = chat["id"]
            else:
                created = await self._client.send({
                    "@type": "createNewBasicGroupChat", "user_ids": user_ids, "title": title,
                    "message_auto_delete_time": 0})
                chat_id = created["chat_id"]
                failed = (created.get("failed_to_add_members") or {}).get(
                    "failed_to_add_members") or []
                if failed:
                    self.failed.emit(f"{len(failed)} people couldn't be added "
                                     "(their privacy settings)")
        except TdError as e:
            self.failed.emit(e.message)
            return
        self.created.emit(chat_id)

    # --- the invite link --------------------------------------------------------------------

    @Slot("QVariant", result=str)
    def inviteLink(self, chat_id: Any) -> str:
        """The primary link if known; asks TDLib otherwise (changed then fires)."""
        chat_id = int(chat_id or 0)
        if chat_id not in self._links and self.role(chat_id) in ("owner", "admin"):
            self._links[chat_id] = ""
            self._spawn(self._load_link(chat_id))
        return self._links.get(chat_id, "")

    @Slot("QVariant")
    def copyInviteLink(self, chat_id: Any) -> None:
        link = self._links.get(int(chat_id or 0), "")
        if link:
            QGuiApplication.clipboard().setText(link)

    @Slot("QVariant")
    def resetInviteLink(self, chat_id: Any) -> None:
        self._spawn(self._replace_link(int(chat_id or 0)))

    async def _load_link(self, chat_id: int) -> None:
        chat = self._chats.chats.get(chat_id)
        if chat is None:
            return
        try:
            if chat.type == "group":
                full = await self._client.send({"@type": "getBasicGroupFullInfo",
                                                "basic_group_id": chat.peer_id})
            else:
                full = await self._client.send({"@type": "getSupergroupFullInfo",
                                                "supergroup_id": chat.peer_id})
        except TdError as e:
            log.info("No invite link for %s: %s", chat_id, e)
            return
        link = (full.get("invite_link") or {}).get("invite_link", "")
        if link:
            self._links[chat_id] = link
            self.changed.emit()
        else:
            await self._replace_link(chat_id)  # none yet: make one

    async def _replace_link(self, chat_id: int) -> None:
        try:
            link = await self._client.send({"@type": "replacePrimaryChatInviteLink",
                                            "chat_id": chat_id})
        except TdError as e:
            self.failed.emit(e.message)
            return
        self._links[chat_id] = link.get("invite_link", "")
        self.changed.emit()

    # --- members ----------------------------------------------------------------------------

    @Slot("QVariant", "QVariantList")
    def addMembers(self, chat_id: Any, user_ids: list[Any]) -> None:
        self._run({"@type": "addChatMembers", "chat_id": int(chat_id),
                   "user_ids": [int(u) for u in user_ids]})

    @Slot("QVariant", "QVariant", bool)
    def setAdmin(self, chat_id: Any, user_id: Any, admin: bool) -> None:
        status = ({"@type": "chatMemberStatusAdministrator", "can_be_edited": True,
                   "rights": ADMIN_RIGHTS} if admin
                  else {"@type": "chatMemberStatusMember", "member_until_date": 0})
        self._run({"@type": "setChatMemberStatus", "chat_id": int(chat_id),
                   "member_id": {"@type": "messageSenderUser", "user_id": int(user_id)},
                   "status": status})

    @Slot("QVariant", "QVariant")
    def removeMember(self, chat_id: Any, user_id: Any) -> None:
        self._run({"@type": "banChatMember", "chat_id": int(chat_id),
                   "member_id": {"@type": "messageSenderUser", "user_id": int(user_id)},
                   "banned_until_date": 0, "revoke_messages": False})

    @Slot("QVariant", str, str)
    def editInfo(self, chat_id: Any, title: str, description: str) -> None:
        chat = self._chats.chats.get(int(chat_id or 0))
        if chat is None:
            return
        if title.strip() and title.strip() != chat.title:
            self._run({"@type": "setChatTitle", "chat_id": chat.id, "title": title.strip()})
        self._run({"@type": "setChatDescription", "chat_id": chat.id,
                   "description": description.strip()})

    @Slot("QVariant")
    def deleteChat(self, chat_id: Any) -> None:
        self._run({"@type": "deleteChat", "chat_id": int(chat_id)})

    def _run(self, request: dict[str, Any]) -> None:
        async def run() -> None:
            try:
                await self._client.send(request)
            except TdError as e:
                log.warning("%s failed: %s", request["@type"], e)
                self.failed.emit(e.message)
                return
            self.changed.emit()

        self._spawn(run())

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
