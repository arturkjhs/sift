"""GUI entry point: PySide6 + QML on a qasync event loop."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from pathlib import Path

import qasync
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine

from .config import APP_ID, APP_NAME, Settings, build_info, load_settings
from .models.chat_list import ChatListModel
from .models.emoji import EmojiModel
from .models.folders import FolderModel
from .models.messages import MessageListModel
from .models.search import SearchModel
from .models.stickers import StickerModel
from .services.ai import AiService
from .services.ai_store import AiStore
from .services.embeddings import Embedder, FastEmbedder, semantic_available
from .services.openrouter import OpenRouter, mask_key
from .services.search import SearchService
from .services.search_index import SearchIndex
from .store.chats import MAIN, ChatStore
from .store.emoji import EmojiCatalog
from .store.files import FileManager
from .store.stickers import StickerStore
from .store.users import UserStore
from .td import AuthError, AuthFlow, TdError, TdHub, TdJson, TdlibParams
from .td.client import TdLib
from .ui.ai_controller import AiController
from .ui.auth_controller import AuthController
from .ui.icons import IconProvider
from .ui.images import TdImageProvider
from .ui.shell import ShellController
from .ui.voice_player import VoicePlayer

log = logging.getLogger(__name__)

QML_IMPORT_DIR = Path(__file__).resolve().parent / "ui" / "qml"
APP_ICON = Path(__file__).resolve().parent / "ui" / "app-icon.svg"


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
    ) -> None:
        if lib is None:
            native = TdJson()
            native.execute(
                {"@type": "setLogVerbosityLevel", "new_verbosity_level": settings.td_log_level}
            )
            lib = native
        self.hub = TdHub(lib)
        self.client = self.hub.create_client()
        # Subscribers must exist before the first request, otherwise early updates are lost.
        self.files = FileManager(self.client)
        self.chats = ChatStore(self.client, self.files)
        self.users = UserStore(self.client, self.files)
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
        self.chat_list = ChatListModel(self.chats, self.users)
        self.folders = FolderModel(self.chats)
        if router is None and settings.openrouter_api_key:
            router = OpenRouter(settings.openrouter_api_key)
        self.ai_service = AiService(
            self.client, self.chats, self.users,
            ai_store or AiStore(settings.ai_db_path), router,
            settings.summary_model, settings.transcription_model,
            key_hint=mask_key(settings.openrouter_api_key) if router else "",
            key_source=settings.openrouter_key_source if router else "",
        )
        self.ai = AiController(self.ai_service, self.chats)
        self.messages = MessageListModel(self.client, self.chats, self.users, self.ai_service)
        if embedder is None and semantic_available():
            embedder = FastEmbedder(settings.embedding_model, settings.models_dir)
        self.search_service = SearchService(
            self.client, self.chats, self.users,
            search_index or SearchIndex(settings.search_db_path), embedder, self.ai_service,
        )
        self.search = SearchModel(self.search_service, self.chats)
        self.sticker_store = StickerStore(self.client, self.files)
        self.stickers = StickerModel(self.sticker_store, self.files)
        self.emojis = EmojiModel(EmojiCatalog(settings.data_dir / "recent-emoji.json"))
        self.voice = VoicePlayer(self.client, self.files)

    async def start(self) -> None:
        try:
            await self.client.send({"@type": "getOption", "name": "version"})
        except TdError:
            pass  # only needed to start the TDLib instance
        try:
            await self.auth_flow.run()
        except AuthError as e:
            self.auth.set_failed(str(e))
            return
        self.auth.set_ready()
        self.chat_list.setList(MAIN)
        self.search_service.start()

    async def close(self) -> None:
        self.voice.stop()
        await self.search_service.close()
        await self.ai_service.close()
        await self.client.close()
        self.hub.stop()


def create_engine(
    session: Session,
    shell: ShellController,
    on_warnings: Callable[[list[str]], None] | None = None,
) -> QQmlApplicationEngine:
    engine = QQmlApplicationEngine()
    engine.warnings.connect(
        lambda errors: (on_warnings or _log_qml_warnings)([e.toString() for e in errors])
    )
    engine.addImportPath(str(QML_IMPORT_DIR))
    images = TdImageProvider(session.files)
    engine.addImageProvider("tg", images)
    icons = IconProvider()
    engine.addImageProvider("icon", icons)
    # Context properties don't own their objects: keep Python refs alive as long as the engine.
    engine._tgclient_refs = (shell, images, icons)  # type: ignore[attr-defined]
    context = engine.rootContext()
    context.setContextProperty("shell", shell)
    context.setContextProperty("auth", session.auth)
    context.setContextProperty("chatList", session.chat_list)
    context.setContextProperty("folders", session.folders)
    context.setContextProperty("messages", session.messages)
    context.setContextProperty("voice", session.voice)
    context.setContextProperty("ai", session.ai)
    context.setContextProperty("search", session.search)
    context.setContextProperty("emojis", session.emojis)
    context.setContextProperty("stickers", session.stickers)
    engine.loadFromModule("TgClient", "Main")
    return engine


def _log_qml_warnings(messages: list[str]) -> None:
    for message in messages:
        log.warning("QML: %s", message)


async def amain(app: QGuiApplication) -> int:
    settings = load_settings()
    logging.getLogger().setLevel(settings.log_level)
    settings.database_dir.mkdir(parents=True, exist_ok=True)
    settings.files_dir.mkdir(parents=True, exist_ok=True)

    # Closing the window must not stop the Qt loop before TDLib is closed: QML calls
    # shell.requestQuit(), we close TDLib, and returning from amain() exits the loop.
    app.setQuitOnLastWindowClosed(False)
    quit_event = asyncio.Event()
    app.aboutToQuit.connect(quit_event.set)
    shell = ShellController(quit_event)

    session = Session(settings)
    engine = create_engine(session, shell)
    if not engine.rootObjects():
        log.error("Failed to load QML")
        await session.close()
        return 1

    start = asyncio.ensure_future(session.start())
    try:
        await quit_event.wait()
    finally:
        start.cancel()
        await session.close()
        del engine
    return 0


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if "--self-test" in sys.argv:
        from .selftest import run

        sys.exit(run())
    if "--version" in sys.argv:
        print(f"{APP_NAME} {build_info().get('VERSION', 'dev')}")
        return
    app = QGuiApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setDesktopFileName(APP_ID)  # Wayland: matches the window to the .desktop entry
    app.setWindowIcon(QIcon(str(APP_ICON)))

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        try:
            code = loop.run_until_complete(amain(app))
        except RuntimeError as e:
            # Qt loop ended from outside (e.g. OS-level quit). TDLib's database survives this.
            log.warning("Event loop stopped early: %s", e)
            code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
