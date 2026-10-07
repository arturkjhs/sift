"""What the chat list's context menu does to a chat: pin, mute, archive, mark read/unread,
clear the history, leave or delete. Exposed to QML as `chatActions`."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from ..store.chats import ARCHIVE, MAIN, ChatStore, chat_list_obj
from ..td.client import TdClient, TdError

log = logging.getLogger(__name__)

MUTE_FOREVER = 366 * 86400  # TDLib treats a year or more as "forever"

_DEFAULT_SETTINGS: dict[str, Any] = {
    "@type": "chatNotificationSettings", "use_default_mute_for": True, "mute_for": 0,
    "use_default_sound": True, "sound_id": "0", "use_default_show_preview": True,
    "show_preview": True, "use_default_mute_stories": True, "mute_stories": False,
    "use_default_story_sound": True, "story_sound_id": "0",
    "use_default_show_story_poster": True, "show_story_poster": True,
    "use_default_disable_pinned_message_notifications": True,
    "disable_pinned_message_notifications": False,
    "use_default_disable_mention_notifications": True, "disable_mention_notifications": False,
}


class ChatActions(QObject):
    failed = Signal(str)  # something didn't work: a short message for the user
    left = Signal("QVariant")  # left or deleted a chat: close it if it's open

    def __init__(self, client: TdClient, chats: ChatStore, parent: Any = None) -> None:
        super().__init__(parent)
        self._client = client
        self._chats = chats
        self._tasks: set[asyncio.Task[Any]] = set()

    @Slot("QVariant", result="QVariantMap")
    def state(self, chat_id: Any) -> dict[str, Any]:
        """What the menu shows for a chat (asked when it opens)."""
        chat = self._chats.chats.get(int(chat_id or 0))
        if chat is None:
            return {}
        return {
            "pinned": any(p.is_pinned for k, p in chat.positions.items()
                          if k in (MAIN, ARCHIVE) or k.startswith("folder:")),
            "muted": self._chats.is_muted(chat),
            "archived": ARCHIVE in chat.positions,
            "unread": chat.unread_count > 0 or chat.is_marked_as_unread,
            "type": chat.type,
            "canDeleteForAll": chat.can_delete_for_all,
        }

    @Slot("QVariant", str, bool)
    def setPinned(self, chat_id: Any, list_key: str, pinned: bool) -> None:
        self._run({"@type": "toggleChatIsPinned", "chat_list": chat_list_obj(list_key or MAIN),
                   "chat_id": int(chat_id), "is_pinned": pinned},
                  "Can't pin more chats" if pinned else "Unpinning failed")

    @Slot("QVariant", int)
    def mute(self, chat_id: Any, seconds: int) -> None:
        """seconds: 0 unmutes, -1 forever."""
        chat = self._chats.chats.get(int(chat_id or 0))
        if chat is None:
            return
        settings = {**_DEFAULT_SETTINGS, **chat.notification_settings,
                    "@type": "chatNotificationSettings", "use_default_mute_for": False,
                    "mute_for": MUTE_FOREVER if seconds < 0 else max(0, seconds)}
        self._run({"@type": "setChatNotificationSettings", "chat_id": chat.id,
                   "notification_settings": settings}, "Changing notifications failed")

    @Slot("QVariant", bool)
    def setArchived(self, chat_id: Any, archived: bool) -> None:
        self._run({"@type": "addChatToList", "chat_id": int(chat_id),
                   "chat_list": chat_list_obj(ARCHIVE if archived else MAIN)},
                  "Moving the chat failed")

    @Slot("QVariant", bool)
    def setUnread(self, chat_id: Any, unread: bool) -> None:
        chat = self._chats.chats.get(int(chat_id or 0))
        if chat is None:
            return
        if unread:
            self._run({"@type": "toggleChatIsMarkedAsUnread", "chat_id": chat.id,
                       "is_marked_as_unread": True}, "Marking failed")
            return
        if chat.is_marked_as_unread:
            self._run({"@type": "toggleChatIsMarkedAsUnread", "chat_id": chat.id,
                       "is_marked_as_unread": False}, "Marking failed")
        last = (chat.last_message or {}).get("id")
        if last and chat.unread_count:
            self._run({"@type": "viewMessages", "chat_id": chat.id, "message_ids": [last],
                       "source": None, "force_read": True}, "Marking failed")
        if chat.unread_mention_count:
            self._run({"@type": "readAllChatMentions", "chat_id": chat.id}, "Marking failed")

    @Slot("QVariant", bool)
    def clearHistory(self, chat_id: Any, for_everyone: bool) -> None:
        self._run({"@type": "deleteChatHistory", "chat_id": int(chat_id),
                   "remove_from_chat_list": False, "revoke": for_everyone},
                  "Clearing the history failed")

    @Slot("QVariant", bool)
    def leave(self, chat_id: Any, for_everyone: bool = False) -> None:
        """Groups and channels: leave. Private chats: delete (for both, if asked)."""
        chat = self._chats.chats.get(int(chat_id or 0))
        if chat is None:
            return
        if chat.type in ("private", "secret"):
            request = {"@type": "deleteChatHistory", "chat_id": chat.id,
                       "remove_from_chat_list": True, "revoke": for_everyone}
        else:
            request = {"@type": "leaveChat", "chat_id": chat.id}
        self.left.emit(chat.id)
        self._run(request, "Leaving the chat failed")

    def _run(self, request: dict[str, Any], error: str) -> None:
        async def run() -> None:
            try:
                await self._client.send(request)
            except TdError as e:
                log.warning("%s: %s", request["@type"], e)
                self.failed.emit(f"{error}: {e.message}")

        task = asyncio.ensure_future(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
