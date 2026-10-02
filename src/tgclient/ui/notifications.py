"""System notifications and the unread badge on the Dock / launcher icon.

Backends:
- macOS app bundle: UserNotifications (pyobjc). Needs a bundle id, so it's only used in the
  packaged .app; `uv run` falls back to `osascript` (no click handling, no withdrawal).
- Linux: org.freedesktop.Notifications over D-Bus (jeepney, asyncio).
The badge is QGuiApplication.setBadgeNumber (Dock on macOS, Unity LauncherEntry on Linux).
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import html
import logging
import os
import sys
from collections.abc import Callable
from typing import Any, Protocol

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication

from ..config import APP_ID, APP_NAME
from ..prefs import Prefs
from ..store.chats import MAIN, ChatStore
from ..store.notifications import Notice, Notifier
from ..store.users import UserStore
from ..td.client import TdClient

log = logging.getLogger(__name__)


class Backend(Protocol):
    on_activated: Callable[[str], None]  # notification key clicked

    def show(self, notice: Notice) -> None: ...
    def withdraw(self, keys: list[str]) -> None: ...
    def close(self) -> None: ...


class NullBackend:
    def __init__(self) -> None:
        self.on_activated: Callable[[str], None] = lambda key: None
        self.shown: list[Notice] = []
        self.withdrawn: list[str] = []

    def show(self, notice: Notice) -> None:
        self.shown.append(notice)

    def withdraw(self, keys: list[str]) -> None:
        self.withdrawn.extend(keys)

    def close(self) -> None:
        pass


class NotificationController(QObject):
    """QML-facing settings, the badge, and routing clicks back to the chat."""

    settingsChanged = Signal()
    chatRequested = Signal("QVariant")  # chat id: bring the window up and open it
    unreadChanged = Signal(int)

    def __init__(
        self, client: TdClient, chats: ChatStore, users: UserStore, prefs: Prefs,
        backend: Backend | None = None, open_chat: Callable[[], int] = lambda: 0,
        parent: Any = None, account: str = "", shared_backend: bool = False,
    ) -> None:
        """With several accounts the backend is shared: keys get the account as a prefix,
        AccountManager routes clicks to handle_activation() and sums the badge."""
        super().__init__(parent)
        self._chats = chats
        self._prefs = prefs
        self._open_chat = open_chat
        self._prefix = f"{account}/" if account else ""
        self._backend = backend or default_backend()
        self.manage_badge = not shared_backend
        if not shared_backend:
            self._backend.on_activated = self.handle_activation
        self._chat_of: dict[str, int] = {}  # notification key -> chat id
        self.notifier = Notifier(client, chats, users, self, suppress=self._suppress)
        self.notifier.enabled = bool(prefs.get("notifications"))
        self.notifier.show_preview = bool(prefs.get("notification_preview"))
        self._badge = -1
        chats.subscribe(self._on_chats)

    async def start(self) -> None:
        await self.notifier.start()
        self._update_badge()

    def close(self) -> None:
        if self.manage_badge:
            self._set_badge(0)
            self._backend.close()
        else:
            self.notifier.withdraw_all()

    @property
    def unread(self) -> int:
        return self._chats.unread_messages.get(MAIN, 0)

    # --- QML API ----------------------------------------------------------------------------

    @Property(bool, notify=settingsChanged)
    def enabled(self) -> bool:
        return self.notifier.enabled

    @Slot(bool)
    def setEnabled(self, value: bool) -> None:
        self.notifier.enabled = value
        self._prefs.set("notifications", value)
        if not value:
            self.notifier.withdraw_all()
        self.settingsChanged.emit()

    @Property(bool, notify=settingsChanged)
    def showPreview(self) -> bool:
        return self.notifier.show_preview

    @Slot(bool)
    def setShowPreview(self, value: bool) -> None:
        self.notifier.show_preview = value
        self._prefs.set("notification_preview", value)
        self.settingsChanged.emit()

    @Slot()
    def sendTest(self) -> None:
        self.show(Notice(key="test", chat_id=0, message_id=0, title=APP_NAME, subtitle="",
                         body="Notifications work", silent=False))

    # --- NotificationSink -------------------------------------------------------------------

    def show(self, notice: Notice) -> None:
        notice = dataclasses.replace(notice, key=self._prefix + notice.key)
        self._chat_of[notice.key] = notice.chat_id
        self._backend.show(notice)

    def withdraw(self, keys: list[str]) -> None:
        keys = [self._prefix + key for key in keys]
        for key in keys:
            self._chat_of.pop(key, None)
        self._backend.withdraw(keys)

    def handle_activation(self, key: str) -> bool:
        """A notification was clicked; True if it was one of ours."""
        chat_id = self._chat_of.get(key)
        if chat_id:
            self.chatRequested.emit(chat_id)
            return True
        return False

    # --- internals --------------------------------------------------------------------------

    def _suppress(self, chat_id: int) -> bool:
        app = QGuiApplication.instance()
        active = isinstance(app, QGuiApplication) and (
            app.applicationState() == Qt.ApplicationState.ApplicationActive)
        return active and chat_id == self._open_chat()

    def _on_chats(self, kind: str, payload: Any) -> None:
        if kind == "unread_messages" and payload == MAIN:
            self._update_badge()

    def _update_badge(self) -> None:
        self.unreadChanged.emit(self.unread)
        if self.manage_badge:
            self._set_badge(self.unread)

    def set_badge(self, count: int) -> None:
        self._set_badge(count)

    def _set_badge(self, count: int) -> None:
        app = QGuiApplication.instance()
        if count != self._badge and isinstance(app, QGuiApplication):
            self._badge = count
            app.setBadgeNumber(count)


def default_backend() -> Backend:
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return NullBackend()  # tests and headless runs
    if sys.platform == "darwin":
        if _in_app_bundle():
            try:
                return MacBackend()
            except Exception:
                log.exception("UserNotifications unavailable, using osascript")
        return ScriptBackend()
    if sys.platform.startswith("linux"):
        try:
            return DBusBackend()
        except ImportError:
            log.warning("jeepney missing: notifications are off")
    return NullBackend()


def _in_app_bundle() -> bool:
    """Our own bundle: the packaged .app or the dev bundle (devbundle.py). Not e.g. Homebrew's
    Python.app, which would ask for permission and notify as "Python"."""
    try:
        from Foundation import NSBundle
    except ImportError:
        return False
    from ..devbundle import DEV_BUNDLE_ID

    return str(NSBundle.mainBundle().bundleIdentifier() or "") in (APP_ID, DEV_BUNDLE_ID)


class MacBackend:
    """UNUserNotificationCenter. Delegate callbacks arrive on a background queue."""

    def __init__(self) -> None:
        import UserNotifications as UN

        self.on_activated: Callable[[str], None] = lambda key: None
        self._UN = UN
        loop = asyncio.get_event_loop()
        self._center = UN.UNUserNotificationCenter.currentNotificationCenter()
        self._delegate = _mac_delegate_class().alloc().init()
        self._delegate.on_click = lambda key: loop.call_soon_threadsafe(self.on_activated, key)
        self._center.setDelegate_(self._delegate)
        self._center.removeAllDeliveredNotifications()  # keys of a previous run mean nothing
        self._center.requestAuthorizationWithOptions_completionHandler_(
            UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound,
            lambda granted, error: log.info("Notifications allowed: %s", granted))

    def show(self, notice: Notice) -> None:
        UN = self._UN
        content = UN.UNMutableNotificationContent.alloc().init()
        content.setTitle_(notice.title)
        if notice.subtitle:
            content.setSubtitle_(notice.subtitle)
        content.setBody_(notice.body)
        content.setThreadIdentifier_(str(notice.chat_id))
        if not notice.silent:
            content.setSound_(UN.UNNotificationSound.defaultSound())
        request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(
            notice.key, content, None)
        self._center.addNotificationRequest_withCompletionHandler_(request, None)

    def withdraw(self, keys: list[str]) -> None:
        self._center.removeDeliveredNotificationsWithIdentifiers_(keys)

    def close(self) -> None:
        self._center.setDelegate_(None)


@functools.cache
def _mac_delegate_class() -> Any:
    """UNUserNotificationCenterDelegate. An Objective-C class can be defined only once per
    process, hence the cache."""
    import UserNotifications as UN
    from Foundation import NSObject

    class TgcNotificationDelegate(NSObject):  # type: ignore[misc, valid-type]
        on_click: Callable[[str], None] = staticmethod(lambda key: None)

        def userNotificationCenter_willPresentNotification_withCompletionHandler_(
                self, center: Any, notification: Any, handler: Any) -> None:
            handler(UN.UNNotificationPresentationOptionBanner
                    | UN.UNNotificationPresentationOptionList
                    | UN.UNNotificationPresentationOptionSound)

        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                self, center: Any, response: Any, handler: Any) -> None:
            self.on_click(str(response.notification().request().identifier()))
            handler()

    return TgcNotificationDelegate


class ScriptBackend:
    """Development fallback on macOS outside an app bundle: `osascript display notification`."""

    _SCRIPT = ("on run argv\n"
               "display notification (item 3 of argv) with title (item 1 of argv)"
               " subtitle (item 2 of argv)\n"
               "end run")

    def __init__(self) -> None:
        self.on_activated: Callable[[str], None] = lambda key: None

    def show(self, notice: Notice) -> None:
        asyncio.ensure_future(self._run(notice))

    async def _run(self, notice: Notice) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                "osascript", "-e", self._SCRIPT, notice.title, notice.subtitle, notice.body,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await process.wait()
        except OSError as e:
            log.warning("osascript failed: %s", e)

    def withdraw(self, keys: list[str]) -> None:
        pass

    def close(self) -> None:
        pass


class DBusBackend:
    """org.freedesktop.Notifications: Notify / CloseNotification, ActionInvoked for clicks."""

    BUS_NAME = "org.freedesktop.Notifications"
    PATH = "/org/freedesktop/Notifications"

    def __init__(self) -> None:
        import jeepney  # noqa: F401 - fail early (ImportError) if missing

        self.on_activated: Callable[[str], None] = lambda key: None
        self._router: Any = None
        self._connecting: asyncio.Future[Any] | None = None
        self._ids: dict[str, int] = {}  # key -> server id
        self._keys: dict[int, str] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._broken = False

    def show(self, notice: Notice) -> None:
        self._spawn(self._notify(notice))

    def withdraw(self, keys: list[str]) -> None:
        ids = [self._ids.pop(key) for key in keys if key in self._ids]
        for server_id in ids:
            self._keys.pop(server_id, None)
        if ids:
            self._spawn(self._close_ids(ids))

    def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()

    def _spawn(self, coro: Any) -> None:
        if self._broken:
            coro.close()
            return
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _call(self, method: str, signature: str, body: tuple[Any, ...]) -> Any:
        from jeepney import DBusAddress, new_method_call

        address = DBusAddress(self.PATH, bus_name=self.BUS_NAME, interface=self.BUS_NAME)
        return new_method_call(address, method, signature, body)

    def notify_message(self, notice: Notice, replaces: int = 0) -> Any:
        body = html.escape(notice.body, quote=False)  # servers may parse simple markup
        if notice.subtitle:
            body = f"{html.escape(notice.subtitle, quote=False)}: {body}"
        hints = {"desktop-entry": ("s", APP_ID), "category": ("s", "im.received"),
                 "suppress-sound": ("b", notice.silent)}
        return self._call("Notify", "susssasa{sv}i", (
            APP_NAME, replaces, APP_ID, notice.title, body, ["default", "Open"], hints, -1))

    async def _connect(self) -> Any:
        if self._router is not None:
            return self._router
        if self._connecting is None:
            self._connecting = asyncio.ensure_future(self._open())
        return await asyncio.shield(self._connecting)

    async def _open(self) -> Any:
        from jeepney import MatchRule, message_bus
        from jeepney.io.asyncio import DBusRouter, Proxy, open_dbus_connection

        router = DBusRouter(await open_dbus_connection("SESSION"))
        rule = MatchRule(type="signal", interface=self.BUS_NAME, path=self.PATH)
        await Proxy(message_bus, router).AddMatch(rule)
        queue: asyncio.Queue[Any] = asyncio.Queue()
        handle = router.filter(rule, queue=queue)
        self._spawn(self._listen(handle, queue))
        self._router = router
        return router

    async def _listen(self, handle: Any, queue: asyncio.Queue[Any]) -> None:
        from jeepney import HeaderFields

        with handle:
            while True:
                message = await queue.get()
                member = message.header.fields.get(HeaderFields.member)
                if member == "ActionInvoked":
                    key = self._keys.get(message.body[0])
                    if key is not None:
                        self.on_activated(key)
                elif member == "NotificationClosed":
                    key = self._keys.pop(message.body[0], None)
                    if key is not None:
                        self._ids.pop(key, None)

    async def _notify(self, notice: Notice) -> None:
        try:
            router = await self._connect()
            reply = await router.send_and_get_reply(self.notify_message(notice))
        except Exception as e:  # noqa: BLE001 - no session bus, no server, sandbox
            log.warning("D-Bus notifications unavailable: %s", e)
            self._broken = True
            return
        if reply.body:
            server_id = int(reply.body[0])
            self._ids[notice.key] = server_id
            self._keys[server_id] = notice.key

    async def _close_ids(self, ids: list[int]) -> None:
        try:
            router = await self._connect()
            for server_id in ids:
                await router.send_and_get_reply(
                    self._call("CloseNotification", "u", (server_id,)))
        except Exception as e:  # noqa: BLE001 - best effort
            log.debug("CloseNotification failed: %s", e)
