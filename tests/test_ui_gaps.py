"""Views added while closing the gaps with the official client (docs/FEATURE_GAPS.md), in the
real QML offscreen: forum topics and the rest as they land. Fails on any QML warning."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

import test_forums
from fakes import FakeLib, auth_state, new_chat, ok, qt_app, wait_until
from test_forums import FORUM, MESSAGES, SUPERGROUP
from test_ui import _all_items, _screenshot

from tgclient.config import Settings


PRIVATE_MESSAGES = [(902, "Will bring the rope on Saturday"), (901, "m5 is my favourite route")]


def _private(mid: int, text: str) -> dict[str, Any]:
    """Olena in the private chat (chat 5), newer than anything in the forum."""
    return {"@type": "message", "id": mid, "chat_id": 5, "date": 1_900_000_000 + mid,
            "sender_id": {"@type": "messageSenderUser", "user_id": 5}, "is_outgoing": False,
            "content": {"@type": "messageText", "text": {"text": text, "entities": []}}}


class Server(test_forums.Server):
    def __call__(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        extra = req.get("@extra")
        match req["@type"]:
            case "getOption":
                return [auth_state("authorizationStateReady"),
                        new_chat(FORUM, "Climbers", 50, "chatTypeSupergroup",
                                 supergroup_id=SUPERGROUP, unread_count=4,
                                 last_message=MESSAGES[60]),
                        {"@type": "updateSupergroup", "supergroup": {
                            "id": SUPERGROUP, "is_forum": True, "is_channel": False,
                            "member_count": 40, "status": {
                                "@type": "chatMemberStatusCreator", "is_member": True}}},
                        {"@type": "updateUser", "user": {"id": 5, "first_name": "Olena",
                                                         "usernames": {
                                                             "active_usernames": ["olena"]}}},
                        {"@type": "updateUser", "user": {"id": 6, "first_name": "Petr",
                                                         "last_name": "Novák"}},
                        {"@type": "updateUser", "user": {"id": 1, "first_name": "Me"}},
                        {"@type": "updateOption", "name": "my_id",
                         "value": {"@type": "optionValueInteger", "value": "1"}},
                        {"@type": "optionValueString", "value": "x", "@extra": extra}]
            case "loadChats":
                return [{"@type": "error", "code": 404, "message": "Not Found", "@extra": extra}]
            case "getChatHistory":
                return [{"@type": "messages", "total_count": 0, "messages": [], "@extra": extra}]
            case "searchChatMessages" if req.get("sender_id") and req["chat_id"] == 5:
                found = [_private(mid, text) for mid, text in PRIVATE_MESSAGES
                         if req["query"].lower() in text.lower()]
                return [{"@type": "foundChatMessages", "total_count": len(found),
                         "messages": found, "next_from_message_id": 0, "@extra": extra}]
            case "searchChatMessages" if req.get("sender_id"):
                query = req["query"].lower()
                found = [MESSAGES[i] for i in sorted(MESSAGES, reverse=True)
                         if query in MESSAGES[i]["content"]["text"]["text"].lower()][:20]
                return [{"@type": "foundChatMessages", "total_count": len(found),
                         "messages": found, "next_from_message_id": 0, "@extra": extra}]
            case "searchChatMessages":
                return [{"@type": "foundChatMessages", "total_count": 0, "messages": [],
                         "next_from_message_id": 0, "@extra": extra}]
            case "getSupergroupFullInfo":
                return [{"@type": "supergroupFullInfo", "description": "Routes, trips and gear.",
                         "member_count": 40, "can_get_members": True, "@extra": extra,
                         "invite_link": {"invite_link": "https://t.me/+AbCdEfClimb"}}]
            case "getContacts":
                return [{"@type": "users", "total_count": 2, "user_ids": [5, 6],
                         "@extra": extra}]
            case "getUserFullInfo":
                return [{"@type": "userFullInfo", "bio": {"text": "Leads on weekends"},
                         "group_in_common_count": 1, "@extra": extra}]
            case "getGroupsInCommon":
                return [{"@type": "chats", "total_count": 1, "chat_ids": [FORUM],
                         "@extra": extra}]
            case "searchChatMembers":
                return [{"@type": "chatMembers", "total_count": 2, "@extra": extra, "members": [
                    {"member_id": {"@type": "messageSenderUser", "user_id": u}}
                    for u in (5, 6)]}]
            case "close":
                return [ok(req), auth_state("authorizationStateClosed")]
        return super().__call__(req)


class GapsViewsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = qt_app()
        from tgclient.app import Session, create_engine
        from tgclient.services.ai_store import AiStore
        from tgclient.services.search_index import SearchIndex
        from tgclient.ui.shell import ShellController

        settings = Settings(api_id=1, api_hash="x", data_dir=Path(tempfile.mkdtemp()),
                            use_test_dc=False, td_log_level=0, log_level="WARNING")
        self.server = Server()
        self.session = Session(settings, lib=FakeLib(self.server), ai_store=AiStore(":memory:"),
                               search_index=SearchIndex(":memory:"))
        self.warnings: list[str] = []
        self.engine = create_engine(self.session, ShellController(asyncio.Event()),
                                    on_warnings=self.warnings.extend)
        self.window = self.engine.rootObjects()[0]
        await self.session.start()
        await wait_until(lambda: (self.pump(), self.session.auth.step)[1] == "ready")

    async def asyncTearDown(self) -> None:
        from tgclient.app import dispose_engine

        dispose_engine(self.engine)
        await self.session.close()

    def pump(self) -> None:
        for _ in range(5):
            self.app.processEvents()

    async def settle(self, seconds: float = 0.3) -> None:
        for _ in range(int(seconds / 0.02)):
            self.pump()
            await asyncio.sleep(0.02)

    def item(self, name: str) -> Any:
        return next(i for i in _all_items(self.window.contentItem()) if i.objectName() == name)

    def open_chat(self, chat_id: int) -> None:
        from PySide6.QtQuick import QQuickItem

        self.window.findChild(QQuickItem, "mainView").setProperty("selectedChatId", chat_id)
        self.session.messages.open(chat_id)

    async def test_forum_topics(self) -> None:
        self.open_chat(FORUM)
        await wait_until(lambda: (self.pump(), self.session.topics.rowCount())[1] == 2)
        await self.settle()
        topic_list = self.item("topicList")
        self.assertTrue(topic_list.isVisible())
        self.assertEqual(self.warnings, [], "QML warnings in the topic list")
        _screenshot(self.window, "forum-topics")

        self.session.messages.openTopic(7)
        await wait_until(lambda: (self.pump(), self.session.messages.rowCount())[1] > 0)
        await self.settle()
        self.assertFalse(topic_list.isVisible())
        self.assertTrue(self.item("topicBack").isVisible())
        _screenshot(self.window, "forum-topic")
        self.session.messages.closeTopic()
        await self.settle(0.1)
        self.assertTrue(topic_list.isVisible())
        self.assertEqual(self.warnings, [], "QML warnings in a topic")


    async def test_invite_dialog_and_join_bar(self) -> None:
        from PySide6.QtCore import QMetaObject, QObject

        self.open_chat(FORUM)
        await self.settle(0.1)
        self.session.messages.inviteReady.emit({
            "link": "https://t.me/+x", "title": "Prague IT", "members": 230,
            "description": "Meetups and talks for developers in Prague.", "channel": False,
            "request": False})
        await self.settle()
        dialog = self.window.findChild(QObject, "inviteDialog")
        self.assertTrue(dialog.property("opened"))
        _screenshot(self.window, "invite")
        QMetaObject.invokeMethod(dialog, "close")
        await self.settle(0.15)
        self.assertEqual(self.warnings, [], "QML warnings in the invite dialog")


    async def test_passcode_settings_and_lock_screen(self) -> None:
        from PySide6.QtCore import QMetaObject, QObject

        from tgclient.ui.lock import LockController
        from tgclient.vault import Vault

        lock = self.engine._tgclient_refs[6]
        self.assertIsInstance(lock, LockController)
        # the test engine has no vault: give it one, as the app does
        lock._vault = Vault(Path(tempfile.mkdtemp()), None)
        lock.changed.emit()
        settings = self.window.findChild(QObject, "settingsDialog")
        QMetaObject.invokeMethod(settings, "open")
        await self.settle()
        form = settings.findChild(QObject, "passcodeForm")
        form.setProperty("editing", True)
        flick = settings.property("contentItem")
        flick.setProperty("contentY", max(0.0, form.mapToItem(
            flick.property("contentItem"), 0, 0).y() - 120))
        await self.settle(0.1)
        _screenshot(self.window, "passcode-settings")
        self.assertEqual(lock.setPasscode("2468", ""), "")
        form.setProperty("editing", False)
        await self.settle(0.1)
        _screenshot(self.window, "passcode-set")
        QMetaObject.invokeMethod(settings, "close")
        await self.settle(0.15)

        lock.lockNow()
        await self.settle()
        screen = self.item("lockScreen")
        self.assertTrue(screen.isVisible())
        _screenshot(self.window, "locked")
        field = self.item("passcodeField")
        field.setProperty("text", "0000")
        QMetaObject.invokeMethod(screen, "tryUnlock")
        await self.settle(0.1)
        self.assertTrue(lock.locked)
        self.assertEqual(self.item("lockError").property("text"), "Wrong passcode")
        field.setProperty("text", "2468")
        QMetaObject.invokeMethod(screen, "tryUnlock")
        await self.settle(0.1)
        self.assertFalse(lock.locked)
        self.assertFalse(screen.isVisible())
        self.assertEqual(self.warnings, [], "QML warnings with the passcode")


    async def test_mention_suggestions(self) -> None:
        self.open_chat(FORUM)
        self.session.messages.openTopic(7)
        await wait_until(lambda: (self.pump(), self.session.messages.rowCount())[1] > 0)
        field = self.item("composerInput")
        field.setProperty("text", "thanks @")
        field.setProperty("cursorPosition", 8)
        await wait_until(lambda: (self.pump(), len(self.session.composer.mentions))[1] == 2)
        await self.settle(0.1)
        popup = self.item("mentionPopup")
        self.assertTrue(popup.isVisible())
        _screenshot(self.window, "mentions")
        from PySide6.QtCore import QMetaObject, Q_ARG

        composer = field
        while composer is not None and composer.property("mentionMatch") is None:
            composer = composer.parentItem()
        QMetaObject.invokeMethod(composer, "insertMention",
                                 Q_ARG("QVariant", self.session.composer.mentions[1]))
        self.pump()
        self.assertEqual(field.property("text"), "thanks [Petr Novák](tg://user?id=6) ")
        self.assertFalse(popup.isVisible())
        self.assertEqual(self.warnings, [], "QML warnings with mentions")


    async def test_profile_panel(self) -> None:
        self.open_chat(FORUM)
        await self.settle(0.1)
        view = self.item("messageView")
        from PySide6.QtCore import QMetaObject, Q_ARG

        QMetaObject.invokeMethod(view, "showProfile", Q_ARG("QVariant", FORUM),
                                 Q_ARG("QVariant", 0))
        await wait_until(lambda: (self.pump(), len(self.session.profile.members))[1] == 2)
        await self.settle()
        self.assertTrue(self.item("profilePanel").isVisible())
        _screenshot(self.window, "profile-chat")
        self.session.profile.open(0, 6)
        await wait_until(lambda: (self.pump(), self.session.profile.description)[1] != "")
        await self.settle()
        _screenshot(self.window, "profile-person")
        self.assertEqual(self.warnings, [], "QML warnings in the profile")


    async def test_polls_in_the_feed(self) -> None:
        import test_polls

        self.open_chat(FORUM)
        self.session.messages.openTopic(7)
        await wait_until(lambda: (self.pump(), self.session.messages.rowCount())[1] > 0)
        topic = {"@type": "messageTopicForum", "forum_topic_id": 7}
        for message in ({**test_polls.poll_message(101, chosen=0), "chat_id": FORUM,
                         "topic_id": topic},
                        {**test_polls.poll_message(102, multiple=True), "chat_id": FORUM,
                         "topic_id": topic},
                        {**MESSAGES[60], "id": 103, "content": test_polls.CHECKLIST}):
            self.session.client._dispatch({"@type": "updateNewMessage", "message": message})
        await self.settle()
        feed = next(i for i in _all_items(self.window.contentItem())
                    if i.metaObject().className().startswith("QQuickListView")
                    and i.property("model") is self.session.messages)
        feed.positionViewAtBeginning()
        await self.settle()
        _screenshot(self.window, "polls")
        self.assertEqual(self.warnings, [], "QML warnings with polls")


    async def test_bot_keyboards(self) -> None:
        import test_keyboards

        bot = {**MESSAGES[60], "id": 104, "reply_markup": test_keyboards.INLINE,
               "content": {"@type": "messageText", "text": {"text": "Rate the route",
                                                            "entities": []}}}
        self.open_chat(FORUM)
        self.session.messages.openTopic(7)
        await wait_until(lambda: (self.pump(), self.session.messages.rowCount())[1] > 0)
        self.session.client._dispatch({"@type": "updateNewMessage", "message": bot})
        self.session.client._dispatch({"@type": "updateChatReplyMarkup", "chat_id": FORUM,
                                       "reply_markup_message": {
                                           **MESSAGES[59], "id": 105,
                                           "reply_markup": test_keyboards.KEYBOARD}})
        await self.settle()
        feed = next(i for i in _all_items(self.window.contentItem())
                    if i.metaObject().className().startswith("QQuickListView")
                    and i.property("model") is self.session.messages)
        feed.positionViewAtBeginning()
        self.session.messages.botAnswer.emit("Thanks for voting!", False)
        await self.settle()
        self.assertTrue(self.item("replyKeyboard").isVisible())
        self.assertTrue(any(i.objectName() == "inlineKeyboard" and i.isVisible()
                            for i in _all_items(self.window.contentItem())))
        _screenshot(self.window, "bot-keyboards")
        self.assertEqual(self.warnings, [], "QML warnings with bot keyboards")


    async def test_channel_post_comments(self) -> None:
        import test_comments

        self.session.client._dispatch(new_chat(test_comments.CHANNEL, "News", 40,
                                               "chatTypeSupergroup", is_channel=True))
        self.open_chat(test_comments.CHANNEL)
        await self.settle(0.1)
        for mid, count in ((20, 3), (21, 0)):
            self.session.client._dispatch({"@type": "updateNewMessage",
                                           "message": test_comments.post(mid, count)})
        await self.settle()
        bars = [i for i in _all_items(self.window.contentItem())
                if i.objectName() == "commentsBar" and i.isVisible()]
        self.assertEqual(len(bars), 2)
        _screenshot(self.window, "comments")
        self.assertEqual(self.warnings, [], "QML warnings with comments")


    async def test_new_group_and_admin_tools(self) -> None:
        from PySide6.QtCore import QMetaObject, QObject, Q_ARG

        self.open_chat(FORUM)
        await self.settle(0.1)
        view = self.item("messageView")
        QMetaObject.invokeMethod(view, "showProfile", Q_ARG("QVariant", FORUM),
                                 Q_ARG("QVariant", 0))
        await wait_until(lambda: (self.pump(), self.session.group_admin.inviteLink(FORUM))[1]
                         != "")
        await self.settle()
        self.assertTrue(self.item("inviteLink").isVisible())
        _screenshot(self.window, "profile-admin")
        dialog = self.window.findChild(QObject, "newChatDialog")
        QMetaObject.invokeMethod(dialog, "start", Q_ARG("QVariant", "group"),
                                 Q_ARG("QVariant", 0))
        await wait_until(lambda: (self.pump(), len(self.session.contacts.rows))[1] == 2)
        await self.settle()
        _screenshot(self.window, "new-group")
        QMetaObject.invokeMethod(dialog, "close")
        await self.settle(0.15)
        self.assertEqual(self.warnings, [], "QML warnings in admin tools")


    async def test_ui_language_switches_live(self) -> None:
        shell = self.engine._tgclient_refs[0]
        self.open_chat(FORUM)
        await self.settle(0.1)

        def texts() -> set[str]:
            return {str(i.property("text")) for i in _all_items(self.window.contentItem())
                    if i.property("text")}

        self.assertIn("All chats", texts())
        self.assertIn("Search chats and messages", {
            str(i.property("placeholder")) for i in _all_items(self.window.contentItem())
            if i.property("placeholder")})
        try:
            for language, expected in (("ru", "Поиск чатов и сообщений"),
                                       ("uk", "Пошук чатів і повідомлень"),
                                       ("cs", "Hledat chaty a zprávy")):
                shell.setLanguage(language)
                await self.settle(0.1)
                placeholders = {str(i.property("placeholder"))
                                for i in _all_items(self.window.contentItem())
                                if i.property("placeholder")}
                self.assertIn(expected, placeholders, language)
                self.assertNotIn("All chats", texts())
                if language == "ru":
                    _screenshot(self.window, "language-ru")
        finally:
            shell.setLanguage("en")
        await self.settle(0.1)
        self.assertEqual(self.warnings, [], "QML warnings when switching languages")


    async def test_person_messages_panel(self) -> None:
        from PySide6.QtCore import QMetaObject, Q_ARG

        self.open_chat(FORUM)
        await self.forums_loaded()
        view = self.item("messageView")
        QMetaObject.invokeMethod(view, "showPersonMessages", Q_ARG("QVariant", FORUM),
                                 Q_ARG("QVariant", "user:5"), Q_ARG("QVariant", "Olena"))
        await wait_until(lambda: (self.pump(), self.session.person_messages.count)[1] == 20)
        await self.settle()
        self.assertTrue(self.item("personMessagesPanel").isVisible())
        _screenshot(self.window, "person-messages")
        self.session.person_messages.setProperty("query", "m5")
        await wait_until(lambda: (self.pump(), self.session.person_messages.count)[1] < 20
                         and not self.session.person_messages.busy)
        await self.settle()
        _screenshot(self.window, "person-messages-query")
        self.assertEqual(self.warnings, [], "QML warnings in the person messages panel")

    async def test_person_messages_all_chats(self) -> None:
        from PySide6.QtCore import QMetaObject

        from tgclient.models.person_messages import Role

        self.session.client._dispatch(new_chat(5, "Olena", 45))
        self.open_chat(FORUM)
        self.session.messages.openTopic(7)
        await wait_until(lambda: (self.pump(), self.session.messages.rowCount())[1] > 0)
        view = self.item("messageView")
        QMetaObject.invokeMethod(view, "openSearch")
        self.session.messages.setChatSearchSender("user:5", "Olena")
        await self.settle()
        button = self.item("allChatsButton")
        self.assertTrue(button.isVisible())
        _screenshot(self.window, "from-chat-all-button")
        button.clicked.emit()
        model = self.session.person_messages
        await wait_until(lambda: (self.pump(), model.chatsSearched)[1] == 2 and not model.busy)
        await self.settle()
        self.assertTrue(model.allChats)
        self.assertTrue(self.item("personCoverage").isVisible())
        kinds = [model.data(model.index(r), Role.Kind) for r in range(model.rowCount())]
        self.assertEqual(kinds[0], "header")
        self.assertEqual(model.data(model.index(0), Role.ChatId), 5)  # newest chat first
        _screenshot(self.window, "person-messages-all-chats")
        QMetaObject.invokeMethod(view, "closeSearch")

        self.session.search.setSender("user:5", "Olena")
        self.session.search._run()
        await wait_until(lambda: (self.pump(), self.session.search.chatsSearched)[1] == 2)
        await self.settle()
        self.assertTrue(self.item("globalCoverage").isVisible())
        _screenshot(self.window, "from-global-all-chats")
        self.session.search.setSender("", "")
        await self.settle(0.1)
        self.assertEqual(self.warnings, [], "QML warnings with all common chats")

    async def forums_loaded(self) -> None:
        await wait_until(lambda: (self.pump(), self.session.topics.rowCount())[1] == 2)


    async def test_from_filter(self) -> None:
        from PySide6.QtCore import QMetaObject

        self.open_chat(FORUM)
        self.session.messages.openTopic(7)
        await wait_until(lambda: (self.pump(), self.session.messages.rowCount())[1] > 0)
        view = self.item("messageView")
        QMetaObject.invokeMethod(view, "openSearch")
        self.session.messages.setChatSearchSender("user:5", "Olena")
        await wait_until(lambda: (self.pump(), self.session.messages.chatSearchCount)[1] > 0)
        await self.settle()
        chat_chip = self.item("chatSenderChip")
        inner = {i.objectName(): i for i in _all_items(chat_chip)}
        self.assertTrue(inner["senderChip"].isVisible())
        _screenshot(self.window, "from-chat")
        picker_button = inner["senderPickButton"]
        self.session.messages.setChatSearchSender("", "")
        await self.settle(0.1)
        self.assertTrue(picker_button.isVisible())
        QMetaObject.invokeMethod(view, "closeSearch")

        self.session.search.setSender("user:5", "Olena")
        await self.settle()
        chip = next(i for i in _all_items(self.window.contentItem())
                    if i.objectName() == "globalSenderChip")
        self.assertTrue(chip.isVisible())
        _screenshot(self.window, "from-global")
        self.session.search.setSender("", "")
        await self.settle(0.1)
        self.assertEqual(self.warnings, [], "QML warnings with the From: filter")


if __name__ == "__main__":
    unittest.main()
