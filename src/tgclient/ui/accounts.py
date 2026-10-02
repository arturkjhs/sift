"""Several Telegram accounts at once: one Session (TDLib client + stores + models) per account,
all running, so every account gets notifications and counts toward the badge. QML shows the
active one: switching re-binds the context properties (app.bind_session).

All sessions share one TdHub (one td_receive per process) and one notification backend;
notification keys carry the account, so clicks are routed back here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..accounts import FIRST, MAX_ACCOUNTS, Account, AccountRegistry
from ..store.format import initials

if TYPE_CHECKING:
    from ..app import Session

log = logging.getLogger(__name__)

AVATAR_COLORS = 7
SessionFactory = Callable[[Account], "Session"]


class AccountManager(QObject):
    activeChanged = Signal()
    accountsChanged = Signal()
    chatRequested = Signal("QVariant")  # a notification of some account was clicked

    def __init__(self, registry: AccountRegistry, factory: SessionFactory | None,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self._registry = registry
        self._factory = factory
        self.sessions: dict[str, Session] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._binder: Callable[[Session], None] = lambda session: None
        self._unbinder: Callable[[Session], None] = lambda session: None
        self._app_active = True
        self.on_badge: Callable[[int], None] | None = None

    @classmethod
    def single(cls, session: Session) -> AccountManager:
        """One fixed session (tests, self-test): no registry file, no adding accounts."""
        manager = cls(AccountRegistry(None), None)
        manager._attach(FIRST, session)
        return manager

    @property
    def active(self) -> Session:
        return self.sessions[self._registry.active]

    def set_binder(self, bind: Callable[[Session], None],
                   unbind: Callable[[Session], None] = lambda session: None) -> None:
        self._binder, self._unbinder = bind, unbind

    def start(self) -> None:
        """Create and start the sessions of all registered accounts."""
        if self._factory is None:
            for key, session in self.sessions.items():
                self._start_session(key, session)
            return
        for account in self._registry.accounts:
            if account.key not in self.sessions:
                self._attach(account.key, self._factory(account))
                self._start_session(account.key, self.sessions[account.key])

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for session in list(self.sessions.values()):
            await session.close()

    # --- QML API ----------------------------------------------------------------------------

    @Property(str, notify=activeChanged)
    def activeKey(self) -> str:
        return self._registry.active

    @Property("QVariantList", notify=accountsChanged)
    def accounts(self) -> list[dict[str, Any]]:
        rows = []
        for account in self._registry.accounts:
            session = self.sessions.get(account.key)
            if session is None:
                continue
            me = session.users.me
            name = me.full_name if me else ""
            avatar = ""
            if me and me.photo_file_id is not None:
                if session.files.path(me.photo_file_id):
                    avatar = session.files.url("avatar", me.photo_file_id)
                else:
                    session.files.download(me.photo_file_id)
            rows.append({
                "key": account.key,
                "name": name or ("Signing in…" if session.auth.step != "ready" else ""),
                "avatar": avatar,
                "initials": initials(name) if name else "?",
                "colorIndex": (me.id if me else 0) % AVATAR_COLORS,
                "unread": session.notifications.unread,
                "active": account.key == self._registry.active,
                "ready": session.auth.step == "ready",
            })
        return rows

    @Property(bool, notify=accountsChanged)
    def canAdd(self) -> bool:
        return self._factory is not None and len(self._registry.accounts) < MAX_ACCOUNTS

    @Property(bool, notify=activeChanged)
    def adding(self) -> bool:
        """The active account is signing in while another one is ready: it can be cancelled."""
        if self.active.auth.step == "ready":
            return False
        return any(s.auth.step == "ready" for k, s in self.sessions.items()
                   if k != self._registry.active)

    @Slot(str)
    def switchTo(self, key: str) -> None:
        if key == self._registry.active or key not in self.sessions:
            return
        old = self.active
        old.messages.close()  # TDLib shouldn't think a chat of a hidden account is open
        old.set_online(False)
        self._registry.set_active(key)
        self._binder(self.active)
        self.active.set_online(self._app_active)
        self.activeChanged.emit()
        self.accountsChanged.emit()

    @Slot()
    def addAccount(self) -> None:
        if not self.canAdd or self._factory is None:
            return
        account = self._registry.add()
        session = self._factory(account)
        self._attach(account.key, session)
        self._start_session(account.key, session)
        self.switchTo(account.key)

    @Slot()
    def cancelAdd(self) -> None:
        """Give up signing in to a new account and go back to a ready one."""
        if not self.adding:
            return
        key = self._registry.active
        back = next(k for k, s in self.sessions.items() if k != key and s.auth.step == "ready")
        self.switchTo(back)
        self._spawn(self._drop(key, log_out=True))

    @Slot()
    def logOut(self) -> None:
        """Log the active account out (TDLib deletes its local data) and forget it."""
        key = self._registry.active
        others = [k for k in self.sessions if k != key]
        if others:
            self.switchTo(others[0])
        self._spawn(self._drop(key, log_out=True, replace=not others))

    @Slot()
    def restartLogin(self) -> None:
        """Start signing in from scratch (e.g. phone number instead of the QR code): TDLib
        can't go back from the QR state, so the client is logged out and recreated."""
        key = self._registry.active
        if self._factory is None or self.active.auth.step == "ready":
            return
        self._spawn(self._drop(key, log_out=True, replace=True))

    def set_app_active(self, active: bool) -> None:
        self._app_active = active
        self.active.set_online(active)

    # --- internals --------------------------------------------------------------------------

    def _attach(self, key: str, session: Session) -> None:
        self.sessions[key] = session
        session.notifications.chatRequested.connect(
            lambda chat_id, key=key: self._open_from_notification(key, chat_id))
        session.notifications.unreadChanged.connect(lambda _count: self._update_badge())
        session.notifications.unreadChanged.connect(lambda _count: self.accountsChanged.emit())
        session.users.subscribe(lambda user_id, s=session: self._on_user(s, user_id))
        session.files.subscribe(lambda file_id, s=session: self._on_file(s, file_id))
        session.auth.changed.connect(self._on_auth_changed)

    def _start_session(self, key: str, session: Session) -> None:
        self._tasks[key] = asyncio.ensure_future(session.start())

    async def _drop(self, key: str, log_out: bool, replace: bool = False) -> None:
        session = self.sessions.get(key)
        if session is None:
            return
        account = self._registry.get(key)
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()
        if log_out:
            await session.log_out()
        if replace and self._factory is not None and account is not None:
            # Same folder (TDLib wiped it on logOut), fresh client: back to the first screen.
            fresh = self._factory(account)
            self.sessions[key] = fresh
            self._attach(key, fresh)
            self._start_session(key, fresh)
            if key == self._registry.active:
                self._binder(fresh)
                self.activeChanged.emit()
        else:
            del self.sessions[key]
            self._registry.remove(key)
        self._unbinder(session)
        await session.close()
        if log_out:
            session.wipe_local()
        self.accountsChanged.emit()
        self._update_badge()

    def _open_from_notification(self, key: str, chat_id: int) -> None:
        self.switchTo(key)
        self.chatRequested.emit(chat_id)

    def handle_notification_click(self, notification_key: str) -> None:
        for session in self.sessions.values():
            if session.notifications.handle_activation(notification_key):
                return

    def _update_badge(self) -> None:
        total = sum(s.notifications.unread for s in self.sessions.values())
        if self.on_badge is not None:
            self.on_badge(total)

    def _on_user(self, session: Session, user_id: int) -> None:
        if user_id == session.users.my_id:
            self.accountsChanged.emit()

    def _on_file(self, session: Session, file_id: int) -> None:
        me = session.users.me
        if me is not None and me.photo_file_id == file_id:
            self.accountsChanged.emit()

    def _on_auth_changed(self) -> None:
        self.accountsChanged.emit()
        self.activeChanged.emit()  # `adding` depends on it

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("Account task failed", exc_info=task.exception())
