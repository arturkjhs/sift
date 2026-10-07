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
                            "member_count": 40}},
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
            case "searchChatMessages":
                return [{"@type": "foundChatMessages", "total_count": 0, "messages": [],
                         "next_from_message_id": 0, "@extra": extra}]
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


if __name__ == "__main__":
    unittest.main()
