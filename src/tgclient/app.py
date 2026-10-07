"""GUI entry point: PySide6 + QML on a qasync event loop."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import logging
import sys
from collections.abc import Callable
from pathlib import Path

import qasync
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine

from .accounts import FIRST, Account, AccountRegistry
from .config import APP_ID, APP_NAME, Settings, build_info, load_settings
from .models.chat_list import ChatListModel
from .models.chat_picker import ChatPickerModel
from .models.composer import ComposerModel
from .models.emoji import EmojiModel
from .models.folders import FolderModel
from .models.messages import MessageListModel
from .models.search import SearchModel
from .models.stickers import StickerModel
from .models.topics import TopicListModel
from .models.viewer import ViewerModel
from .prefs import Prefs
from .services.ai import AiService
from .services.ai_store import AiStore
from .services.embeddings import Embedder, FastEmbedder, semantic_available
from .services.openrouter import OpenRouter, mask_key
from .services.search import SearchService
from .services.search_index import SearchIndex
from .services.updates import UpdateChecker
from .store.chats import MAIN, ChatStore
from .store.custom_emoji import CustomEmojiStore
from .store.emoji import EmojiCatalog
from .store.files import FileManager
from .store.forums import ForumStore
from .store.presence import PresenceStore
from .store.stickers import StickerStore
from .store.users import UserStore
from .td import AuthError, AuthFlow, TdError, TdHub, TdJson, TdlibParams
from .td.client import TdLib
from .ui.accounts import AccountManager
from .ui.ai_controller import AiController
from .ui.animation import register_qml_types
from .ui.auth_controller import AuthController
from .ui.icons import IconProvider
from .ui.lock import LockController
from .ui.images import TdImageProvider
from .ui.notifications import Backend, NotificationController, default_backend
from .ui.recorder import VoiceRecorder
from .ui.shell import ShellController
from .ui.updates import UpdateController
from .ui.voice_player import VoicePlayer
from .vault import SealedFile, Vault, database_exists, system_keyring

log = logging.getLogger(__name__)

QML_IMPORT_DIR = Path(__file__).resolve().parent / "ui" / "qml"
APP_ICON = Path(__file__).resolve().parent / "ui" / "app-icon.svg"
SEAL_INTERVAL = 120  # seconds between writes of sealed AI data (if it changed)


class Session:
    """Everything that lives as long as one logged-in TDLib client."""

    def __init__(
        self,
        settings: Settings,
        lib: TdLib | None = None,
        ai_store: AiStore | None = None,
        router: OpenRouter | None = None,
        search_index: SearchIndex | None = None,
        embedder: Embedder | None = None,
        notification_backend: Backend | None = None,
        hub: TdHub | None = None,
        account_key: str = FIRST,
        prefs: Prefs | None = None,
        shared_notifications: bool = False,
        vault: Vault | None = None,
    ) -> None:
        """`hub`, `prefs` and the notification backend are shared between accounts
        (AccountManager); without them the session owns its own."""
        self.settings = settings
        self.account_key = account_key
        # Encryption: the TDLib database key, and (with a passcode) our databases sealed.
        self.vault = vault
        data_key = vault.data_key(account_key) if vault is not None else None
        self._sealed = [SealedFile(settings.ai_db_path, data_key),
                        SealedFile(settings.search_db_path, data_key)]
        if ai_store is None:
            ai_store = (AiStore(":memory:", self._sealed[0].load()) if data_key
                        else AiStore(settings.ai_db_path))
        if search_index is None:
            search_index = (SearchIndex(":memory:", self._sealed[1].load()) if data_key
                            else SearchIndex(settings.search_db_path))
        self._stores = (ai_store, search_index)
        self._seal_task: asyncio.Task[None] | None = None
        self._rekey: bytes | None = None
        self._own_hub = hub is None
        if hub is None:
            if lib is None:
                lib = native_lib(settings)
            hub = TdHub(lib)
        self.hub = hub
        self.client = self.hub.create_client()
        # Subscribers must exist before the first request, otherwise early updates are lost.
        self.files = FileManager(self.client, account_key)
        self.chats = ChatStore(self.client, self.files)
        self.users = UserStore(self.client, self.files)
        self.presence = PresenceStore(self.client)
        self.forums = ForumStore(self.client)
        self.prefs = prefs or Prefs(settings.data_dir / "prefs.json")
        self.auth = AuthController()
        self.auth_flow = AuthFlow(
            self.client,
            self.auth,
            TdlibParams(
                api_id=settings.api_id,
                api_hash=settings.api_hash,
                database_dir=settings.database_dir,
                files_dir=settings.files_dir,
                use_test_dc=settings.use_test_dc,
            ),
        )
        self.chat_list = ChatListModel(self.chats, self.users, self.presence)
        self.folders = FolderModel(self.chats)
        if router is None and settings.openrouter_api_key:
            router = OpenRouter(settings.openrouter_api_key)
        self.ai_service = AiService(
            self.client, self.chats, self.users, ai_store, router,
            self.prefs.get("summary_model") or settings.summary_model,
            self.prefs.get("transcription_model") or settings.transcription_model,
            key_hint=mask_key(settings.openrouter_api_key) if router else "",
            key_source=settings.openrouter_key_source if router else "",
            cheap_model=self.prefs.get("cheap_model") or settings.cheap_model,
            monthly_limit=float(self.prefs.get("ai_monthly_limit") or 0.0),
            translate_to=(self.prefs.get("ai_language") or self.prefs.get("translate_to")
                          or system_language()),
            read_languages=tuple(self.prefs.get("read_languages") or ()),
        )
        self.ai = AiController(self.ai_service, self.chats, prefs=self.prefs)
        self.ai.topic_info = self._topic_info
        self.custom_emoji = CustomEmojiStore(self.client, self.files)
        self.messages = MessageListModel(self.client, self.chats, self.users, self.ai_service,
                                         self.presence, emoji=self.custom_emoji,
                                         forums=self.forums)
        self.topics = TopicListModel(self.forums, self.chats, self.users, self.messages)
        self.viewer = ViewerModel(self.messages, self.files)
        self.recorder = VoiceRecorder(self.client, self.messages, settings.data_dir / "voice")
        self.composer = ComposerModel(self.client, self.chats, self.messages,
                                      settings.data_dir / "pasted")
        self.chat_picker = ChatPickerModel(self.chats, self.users)
        self.notifications = NotificationController(
            self.client, self.chats, self.users, self.prefs, notification_backend,
            open_chat=lambda: int(self.messages.chatId or 0),
            account=account_key if shared_notifications else "",
            shared_backend=shared_notifications)
        notifier = self.notifications.notifier
        notifier.smart = lambda chat_id: self.ai_service.flag(chat_id, "smart_notify")
        notifier.relevance = self.ai_service.is_relevant
        if embedder is None and semantic_available():
            embedder = FastEmbedder(settings.embedding_model, settings.models_dir)
        self.search_service = SearchService(
            self.client, self.chats, self.users,
            search_index, embedder, self.ai_service,
        )
        self.search = SearchModel(self.search_service, self.chats)
        self.ai_service.searcher = lambda query, chat_id: self.search_service.search(
            query, chat_id, limit=30)
        self.sticker_store = StickerStore(self.client, self.files)
        self.stickers = StickerModel(self.sticker_store, self.files)
        self.emojis = EmojiModel(EmojiCatalog(settings.data_dir / "recent-emoji.json"))
        self.voice = VoicePlayer(self.client, self.files)

    def _topic_info(self, chat_id: int, topic_id: int) -> tuple[str, int]:
        topic = self.forums.get(chat_id, topic_id)
        return (topic.name, topic.last_read_inbox_message_id) if topic else ("", 0)

    def _prepare_database_key(self) -> None:
        """The key TDLib opens its database with. A database from before encryption opens
        with the empty key and gets the new one once logged in (_encrypt_old_database)."""
        vault = self.vault
        if vault is None:
            return
        key = vault.database_key(self.account_key)
        if key is None:
            key = vault.new_key()
            if database_exists(self.settings.database_dir):
                self._rekey, key = key, b""
            else:
                vault.store_database_key(self.account_key, key)
        self.auth_flow.params = dataclasses.replace(self.auth_flow.params,
                                                    database_encryption_key=key)

    async def _encrypt_old_database(self) -> None:
        if self._rekey is None or self.vault is None:
            return
        key, self._rekey = self._rekey, None
        try:
            await self.client.send({"@type": "setDatabaseEncryptionKey",
                                    "new_encryption_key": base64.b64encode(key).decode()})
        except TdError as e:
            log.error("Encrypting the local database failed: %s", e)
            return
        self.vault.store_database_key(self.account_key, key)
        log.info("Local database of %s is encrypted now", self.account_key)

    async def _seal_periodically(self) -> None:
        stores = self._stores
        seen = [store.changes for store in stores]
        while True:
            await asyncio.sleep(SEAL_INTERVAL)
            for index, store in enumerate(stores):
                if store.changes != seen[index]:
                    seen[index] = store.changes
                    data = await asyncio.to_thread(store.snapshot)
                    await asyncio.to_thread(self._sealed[index].save, data)

    def reseal(self, key: bytes | None) -> None:
        """The passcode was set or removed: from now on our databases are written sealed (or
        plain), at the latest when the app closes."""
        for sealed in self._sealed:
            sealed.key = key

    async def start(self) -> None:
        self._prepare_database_key()
        try:
            await self.client.send({"@type": "getOption", "name": "version"})
        except TdError:
            pass  # only needed to start the TDLib instance
        try:
            await self.auth_flow.run()
        except AuthError as e:
            self.auth.set_failed(str(e))
            return
        await self._encrypt_old_database()
        if self._sealed[0].key is not None:
            self._seal_task = asyncio.ensure_future(self._seal_periodically())
        self.auth.set_ready()
        self.chat_list.setList(MAIN)
        self.search_service.start()
        await self.notifications.start()

    def set_online(self, online: bool) -> None:
        """Others see us online only while the window is active (like other clients)."""
        if self.auth.step == "ready" and not self.client.is_closed:
            asyncio.ensure_future(self._send_quietly({
                "@type": "setOption", "name": "online",
                "value": {"@type": "optionValueBoolean", "value": online}}))

    async def _send_quietly(self, request: dict) -> None:
        try:
            await self.client.send(request)
        except TdError as e:
            log.debug("%s failed: %s", request["@type"], e)

    async def log_out(self) -> None:
        """TDLib logs out and deletes its database; the client then closes."""
        try:
            await self.client.send({"@type": "logOut"}, timeout=15)
        except (TdError, TimeoutError) as e:
            log.warning("logOut failed: %s", e)

    async def close(self) -> None:
        self.voice.stop()
        self.notifications.close()
        if self._seal_task is not None:
            self._seal_task.cancel()
        await self.composer.close()
        for sealed, store in zip(self._sealed, self._stores, strict=True):
            if sealed.key is not None or sealed.sealed.exists():
                try:
                    sealed.save(store.snapshot(), final=True)
                except Exception:  # noqa: BLE001 - never lose the close over it
                    log.exception("Saving %s failed", sealed.path.name)
        await self.search_service.close()
        await self.ai_service.close()
        await self.client.close()
        if self._own_hub:
            self.hub.stop()

    def wipe_local(self) -> None:
        """After logging out: our own databases of this account (AI results, search index),
        sealed or not, and the database key."""
        if self.vault is not None:
            self.vault.forget(self.account_key)
        for sealed in self._sealed:
            sealed.key = None  # nothing is saved on close
        for path in (self.settings.ai_db_path, self.settings.search_db_path):
            for candidate in path.parent.glob(path.name + "*"):  # -wal, -shm
                try:
                    candidate.unlink()
                except OSError as e:
                    log.warning("Could not delete %s: %s", candidate, e)


def native_lib(settings: Settings) -> TdJson:
    native = TdJson()
    native.execute({"@type": "setLogVerbosityLevel", "new_verbosity_level": settings.td_log_level})
    return native


def update_checker() -> UpdateChecker | None:
    """Only builds that know their GitHub repository check for updates."""
    import os

    repo = os.environ.get("TGC_UPDATE_REPO", "").strip() or build_info().get("UPDATE_REPO", "")
    return UpdateChecker(repo, build_info().get("VERSION", "dev")) if repo else None


def system_language() -> str:
    """The AI's language by default: the first supported one among the system's UI languages
    (not the region: an English UI in Czechia should not mean Czech), else English."""
    from PySide6.QtCore import QLocale

    from .services.assist import LANGUAGES

    for name in QLocale.system().uiLanguages():
        code = name.replace("_", "-").split("-")[0].lower()
        if code in LANGUAGES:
            return code
    return "en"


def create_engine(
    session: Session,
    shell: ShellController,
    on_warnings: Callable[[list[str]], None] | None = None,
    accounts: AccountManager | None = None,
    updates: UpdateController | None = None,
    lock: LockController | None = None,
) -> QQmlApplicationEngine:
    """`session` is the one shown first; `accounts` (default: just this session) has all."""
    register_qml_types()
    engine = QQmlApplicationEngine()
    engine.warnings.connect(
        lambda errors: (on_warnings or _log_qml_warnings)([e.toString() for e in errors])
    )
    engine.addImportPath(str(QML_IMPORT_DIR))
    accounts = accounts or AccountManager.single(session)
    images = TdImageProvider()
    for each in accounts.sessions.values():
        images.add(each.files)
    engine.addImageProvider("tg", images)
    icons = IconProvider()
    engine.addImageProvider("icon", icons)
    from .ui.qr import QrProvider

    qr = QrProvider()
    engine.addImageProvider("qr", qr)
    # Context properties don't own their objects: keep Python refs alive as long as the engine.
    updates = updates or UpdateController(None, session.prefs,
                                          build_info().get("VERSION", "dev"))
    lock = lock or LockController(None)
    for each in accounts.sessions.values():
        each.notifications.notifier.private = lock.is_private
    engine._tgclient_refs = (  # type: ignore[attr-defined]
        shell, images, icons, qr, accounts, updates, lock)
    context = engine.rootContext()
    context.setContextProperty("shell", shell)
    context.setContextProperty("lock", lock)
    context.setContextProperty("accounts", accounts)
    context.setContextProperty("updates", updates)
    accounts.set_binder(lambda active: bind_session(engine, images, active),
                        lambda gone: images.remove(gone.files.account, gone.files))
    bind_session(engine, images, session)
    engine.loadFromModule("TgClient", "Main")
    return engine


def dispose_engine(engine: QQmlApplicationEngine) -> None:
    """Windows first, then the engine: otherwise clearing the context makes every binding
    re-evaluate against null objects (a burst of TypeErrors on quit)."""
    from PySide6.QtCore import QCoreApplication, QEvent

    for root in engine.rootObjects():
        root.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def bind_session(engine: QQmlApplicationEngine, images: TdImageProvider,
                 session: Session) -> None:
    """Point QML at one account's objects (bindings re-evaluate on the switch)."""
    images.add(session.files)
    context = engine.rootContext()
    context.setContextProperty("auth", session.auth)
    context.setContextProperty("chatList", session.chat_list)
    context.setContextProperty("folders", session.folders)
    context.setContextProperty("messages", session.messages)
    context.setContextProperty("topics", session.topics)
    context.setContextProperty("voice", session.voice)
    context.setContextProperty("ai", session.ai)
    context.setContextProperty("search", session.search)
    context.setContextProperty("emojis", session.emojis)
    context.setContextProperty("stickers", session.stickers)
    context.setContextProperty("composerModel", session.composer)
    context.setContextProperty("chatPicker", session.chat_picker)
    context.setContextProperty("notifications", session.notifications)
    context.setContextProperty("viewer", session.viewer)
    context.setContextProperty("recorder", session.recorder)


def _log_qml_warnings(messages: list[str]) -> None:
    for message in messages:
        log.warning("QML: %s", message)


async def amain(app: QGuiApplication) -> int:
    settings = load_settings()
    logging.getLogger().setLevel(settings.log_level)

    # Closing the window must not stop the Qt loop before TDLib is closed: QML calls
    # shell.requestQuit(), we close TDLib, and returning from amain() exits the loop.
    app.setQuitOnLastWindowClosed(False)
    quit_event = asyncio.Event()
    app.aboutToQuit.connect(quit_event.set)

    prefs = Prefs(settings.data_dir / "prefs.json")
    shell = ShellController(quit_event, prefs)
    vault = Vault(settings.data_dir, system_keyring())
    accounts: AccountManager | None = None
    lock = LockController(vault, prefs,
                          sessions=lambda: list(accounts.sessions.values()) if accounts else [])
    app.installEventFilter(lock)
    if vault.locked and not await unlock_at_launch(lock, shell, quit_event):
        return 0

    hub = TdHub(native_lib(settings))
    backend = default_backend()
    registry = AccountRegistry(settings.data_dir)

    def make_session(account: Account) -> Session:
        account_settings = dataclasses.replace(settings, account=account.folder)
        account_settings.database_dir.mkdir(parents=True, exist_ok=True)
        account_settings.files_dir.mkdir(parents=True, exist_ok=True)
        session = Session(account_settings, hub=hub, account_key=account.key, prefs=prefs,
                          notification_backend=backend, shared_notifications=True, vault=vault)
        session.notifications.notifier.private = lock.is_private
        return session

    accounts = AccountManager(registry, make_session)
    backend.on_activated = accounts.handle_notification_click
    accounts.on_badge = lambda count: app.setBadgeNumber(count)
    accounts.start()
    app.applicationStateChanged.connect(
        lambda state: accounts.set_app_active(state == Qt.ApplicationState.ApplicationActive))
    updates = UpdateController(update_checker(), prefs, build_info().get("VERSION", "dev"))
    updates.quitRequested.connect(shell.requestQuit)
    updates.start()
    engine = create_engine(accounts.active, shell, accounts=accounts, updates=updates, lock=lock)
    if not engine.rootObjects():
        log.error("Failed to load QML")
        await accounts.close()
        hub.stop()
        return 1

    try:
        await quit_event.wait()
    finally:
        # QML first: its bindings must not outlive the objects they read.
        dispose_engine(engine)
        await accounts.close()
        app.setBadgeNumber(0)
        backend.close()
        hub.stop()
    return 0


async def unlock_at_launch(lock: LockController, shell: ShellController,
                           quit_event: asyncio.Event) -> bool:
    """A passcode is set: only the lock screen, until it decrypts the database keys.
    False: the window was closed instead."""
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_IMPORT_DIR))
    engine._tgclient_refs = (lock, shell)  # type: ignore[attr-defined]
    engine.rootContext().setContextProperty("lock", lock)
    engine.rootContext().setContextProperty("shell", shell)
    engine.loadFromModule("TgClient", "LockWindow")
    unlocked = asyncio.Event()
    lock.unlocked.connect(unlocked.set)
    waits = {asyncio.ensure_future(unlocked.wait()), asyncio.ensure_future(quit_event.wait())}
    _, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    dispose_engine(engine)
    return unlocked.is_set()


def install_asyncgen_hooks(loop: asyncio.AbstractEventLoop) -> None:
    """asyncio.run() does this, qasync doesn't: without the hooks an async generator dropped
    half-way (an httpx stream on error or cancel) is closed by the GC outside the loop, which
    prints "async generator ignored GeneratorExit" / "no running event loop"."""
    firstiter = getattr(loop, "_asyncgen_firstiter_hook", None)
    finalizer = getattr(loop, "_asyncgen_finalizer_hook", None)
    if firstiter and finalizer:
        sys.set_asyncgen_hooks(firstiter=firstiter, finalizer=finalizer)


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if "--self-test" in sys.argv:
        from .selftest import run

        sys.exit(run())
    if "--version" in sys.argv:
        print(f"{APP_NAME} {build_info().get('VERSION', 'dev')}")
        return
    from .devbundle import relaunch_in_dev_bundle

    relaunch_in_dev_bundle(sys.argv)  # macOS `uv run`: our icon and notification identity
    app = QGuiApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setDesktopFileName(APP_ID)  # Wayland: matches the window to the .desktop entry
    app.setWindowIcon(QIcon(str(APP_ICON)))

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    install_asyncgen_hooks(loop)
    with loop:
        try:
            code = loop.run_until_complete(amain(app))
            loop.run_until_complete(loop.shutdown_asyncgens())
        except RuntimeError as e:
            # Qt loop ended from outside (e.g. OS-level quit). TDLib's database survives this.
            log.warning("Event loop stopped early: %s", e)
            code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
