"""GUI entry point: PySide6 + QML on a qasync event loop."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from pathlib import Path

import qasync
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from .config import APP_NAME, Settings, load_settings
from .models.chat_list import ChatListModel
from .models.folders import FolderModel
from .models.messages import MessageListModel
from .store.chats import MAIN, ChatStore
from .store.files import FileManager
from .store.users import UserStore
from .td import AuthError, AuthFlow, TdError, TdHub, TdJson, TdlibParams
from .td.client import TdLib
from .ui.auth_controller import AuthController
from .ui.images import TdImageProvider
from .ui.shell import ShellController
from .ui.voice_player import VoicePlayer

log = logging.getLogger(__name__)

QML_IMPORT_DIR = Path(__file__).resolve().parent / "ui" / "qml"


class Session:
    """Everything that lives as long as one logged-in TDLib client."""

    def __init__(self, settings: Settings, lib: TdLib | None = None) -> None:
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
        self.users = UserStore(self.client)
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
        self.messages = MessageListModel(self.client, self.chats, self.users)
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

    async def close(self) -> None:
        self.voice.stop()
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
    # Context properties don't own their objects: keep Python refs alive as long as the engine.
    engine._tgclient_refs = (shell, images)  # type: ignore[attr-defined]
    context = engine.rootContext()
    context.setContextProperty("shell", shell)
    context.setContextProperty("auth", session.auth)
    context.setContextProperty("chatList", session.chat_list)
    context.setContextProperty("folders", session.folders)
    context.setContextProperty("messages", session.messages)
    context.setContextProperty("voice", session.voice)
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
    app = QGuiApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)

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
