"""Forum supergroups and their topics. Qt-free.

A supergroup is a forum when updateSupergroup says `is_forum`. Its topics are loaded on demand
(getForumTopics, when the chat is opened) and kept current from updateForumTopicInfo (name,
icon, closed) and updateForumTopic (unread counters, read marks, pinned). New messages move a
topic up and refresh its last message.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ..td.client import Event, TdClient, TdError

log = logging.getLogger(__name__)

GENERAL_TOPIC = 1  # forum_topic_id of the "General" topic
PAGE = 100

ForumKind = Literal["forum", "topics", "topic"]
Listener = Callable[[ForumKind, Any], None]


@dataclass
class Topic:
    chat_id: int
    id: int  # forum_topic_id
    name: str
    icon_color: int = 0x6FB9F0
    icon_emoji_id: str = ""
    is_general: bool = False
    is_closed: bool = False
    is_hidden: bool = False
    is_pinned: bool = False
    unread_count: int = 0
    unread_mention_count: int = 0
    unread_reaction_count: int = 0
    last_read_inbox_message_id: int = 0
    last_message: dict[str, Any] | None = None
    draft: dict[str, Any] | None = field(default=None, repr=False)


def topic_obj(topic_id: int) -> dict[str, Any] | None:
    """TDLib MessageTopic for sending/searching in a topic; None for the whole chat."""
    return {"@type": "messageTopicForum", "forum_topic_id": topic_id} if topic_id else None


def message_topic_id(message: dict[str, Any]) -> int:
    """The forum topic of a message (General when unmarked in a forum)."""
    topic = message.get("topic_id") or {}
    if topic.get("@type") == "messageTopicForum":
        return int(topic.get("forum_topic_id") or 0)
    return GENERAL_TOPIC


class ForumStore:
    def __init__(self, client: TdClient) -> None:
        self._client = client
        self.forum_supergroups: set[int] = set()
        self.topics: dict[int, dict[int, Topic]] = {}  # chat id -> topic id -> topic
        self._loaded: set[int] = set()
        self._listeners: list[Listener] = []
        handlers: dict[str, Callable[[Event], None]] = {
            "updateSupergroup": self._on_supergroup,
            "updateForumTopicInfo": self._on_topic_info,
            "updateForumTopic": self._on_topic,
            "updateNewMessage": self._on_new_message,
        }
        for update_type, handler in handlers.items():
            client.on(update_type, handler)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def is_forum(self, supergroup_id: int) -> bool:
        return supergroup_id in self.forum_supergroups

    def sorted_topics(self, chat_id: int) -> list[Topic]:
        """Pinned first, then by the last message (newest first); hidden General last."""
        topics = list(self.topics.get(chat_id, {}).values())
        return sorted(topics, key=lambda t: (t.is_pinned, not t.is_hidden, _last_date(t),
                                             _last_id(t)), reverse=True)

    def get(self, chat_id: int, topic_id: int) -> Topic | None:
        return self.topics.get(chat_id, {}).get(topic_id)

    async def load(self, chat_id: int) -> None:
        """All topics of a forum (paged)."""
        offset: dict[str, int] = {"offset_date": 0, "offset_message_id": 0,
                                  "offset_forum_topic_id": 0}
        found: dict[int, Topic] = {}
        for _ in range(20):
            try:
                page = await self._client.send({"@type": "getForumTopics", "chat_id": chat_id,
                                                "query": "", **offset, "limit": PAGE})
            except TdError as e:
                log.warning("getForumTopics(%s) failed: %s", chat_id, e)
                break
            raws = page.get("topics") or []
            for raw in raws:
                topic = _topic(chat_id, raw)
                found[topic.id] = topic
            next_offset = {"offset_date": page.get("next_offset_date", 0),
                           "offset_message_id": page.get("next_offset_message_id", 0),
                           "offset_forum_topic_id": page.get("next_offset_forum_topic_id", 0)}
            if not raws or not any(next_offset.values()) or next_offset == offset:
                break
            offset = next_offset
        self.topics[chat_id] = {**self.topics.get(chat_id, {}), **found}
        self._loaded.add(chat_id)
        self._emit("topics", chat_id)

    # --- updates ----------------------------------------------------------------------------

    def _emit(self, kind: ForumKind, payload: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:
                log.exception("Forum listener failed")

    def _on_supergroup(self, event: Event) -> None:
        group = event["supergroup"]
        was = group["id"] in self.forum_supergroups
        if group.get("is_forum") and not group.get("is_channel"):
            self.forum_supergroups.add(group["id"])
        else:
            self.forum_supergroups.discard(group["id"])
        if was != (group["id"] in self.forum_supergroups):
            self._emit("forum", group["id"])

    def _on_topic_info(self, event: Event) -> None:
        info = event["info"]
        chat_id = info["chat_id"]
        topic = self.get(chat_id, info["forum_topic_id"])
        if topic is None:
            if chat_id not in self._loaded:
                return
            topic = Topic(chat_id, info["forum_topic_id"], "")
            self.topics.setdefault(chat_id, {})[topic.id] = topic
        _apply_info(topic, info)
        self._emit("topic", (chat_id, topic.id))

    def _on_topic(self, event: Event) -> None:
        topic = self.get(event["chat_id"], event["forum_topic_id"])
        if topic is None:
            return
        topic.is_pinned = bool(event.get("is_pinned", topic.is_pinned))
        topic.last_read_inbox_message_id = event.get("last_read_inbox_message_id",
                                                     topic.last_read_inbox_message_id)
        topic.unread_mention_count = event.get("unread_mention_count",
                                               topic.unread_mention_count)
        topic.unread_reaction_count = event.get("unread_reaction_count",
                                                topic.unread_reaction_count)
        topic.draft = event.get("draft_message")
        if topic.last_message and topic.last_read_inbox_message_id >= topic.last_message["id"]:
            topic.unread_count = 0
        self._emit("topic", (topic.chat_id, topic.id))
        # The update has no unread count: ask for the topic (answered from TDLib's cache).
        asyncio.ensure_future(self._refresh(topic.chat_id, topic.id))

    async def _refresh(self, chat_id: int, topic_id: int) -> None:
        try:
            raw = await self._client.send({"@type": "getForumTopic", "chat_id": chat_id,
                                           "forum_topic_id": topic_id})
        except TdError as e:
            log.debug("getForumTopic failed: %s", e)
            return
        topic = self.get(chat_id, topic_id)
        if topic is not None and raw.get("info"):
            fresh = _topic(chat_id, raw)
            topic.unread_count = fresh.unread_count
            topic.last_message = fresh.last_message or topic.last_message
            self._emit("topic", (chat_id, topic_id))

    def _on_new_message(self, event: Event) -> None:
        message = event["message"]
        chat_id = message.get("chat_id")
        topics = self.topics.get(chat_id)
        if not topics:
            return
        topic = topics.get(message_topic_id(message))
        if topic is None:
            return
        topic.last_message = message
        if not message.get("is_outgoing"):
            topic.unread_count += 1
        self._emit("topic", (chat_id, topic.id))


def _last_date(topic: Topic) -> int:
    return int((topic.last_message or {}).get("date") or 0)


def _last_id(topic: Topic) -> int:
    return int((topic.last_message or {}).get("id") or 0)


def _apply_info(topic: Topic, info: dict[str, Any]) -> None:
    icon = info.get("icon") or {}
    topic.name = info.get("name", topic.name)
    topic.icon_color = int(icon.get("color", topic.icon_color) or 0)
    topic.icon_emoji_id = str(icon.get("custom_emoji_id") or "")
    if topic.icon_emoji_id == "0":
        topic.icon_emoji_id = ""
    topic.is_general = bool(info.get("is_general"))
    topic.is_closed = bool(info.get("is_closed"))
    topic.is_hidden = bool(info.get("is_hidden"))


def _topic(chat_id: int, raw: dict[str, Any]) -> Topic:
    info = raw.get("info") or {}
    topic = Topic(chat_id, info.get("forum_topic_id", 0), info.get("name", ""))
    _apply_info(topic, info)
    topic.is_pinned = bool(raw.get("is_pinned"))
    topic.unread_count = raw.get("unread_count", 0)
    topic.unread_mention_count = raw.get("unread_mention_count", 0)
    topic.unread_reaction_count = raw.get("unread_reaction_count", 0)
    topic.last_read_inbox_message_id = raw.get("last_read_inbox_message_id", 0)
    topic.last_message = raw.get("last_message")
    topic.draft = raw.get("draft_message")
    return topic
