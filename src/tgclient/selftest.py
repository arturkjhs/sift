"""`tgclient --self-test`: checks that a build has everything it needs, without logging in.

Meant for packaged apps (CI runs it on every .dmg/AppImage/Flatpak build) and for "it doesn't
start" reports. Loads libtdjson, the QML UI (offscreen if there is no display), the icon
renderer, QtMultimedia, SQLite FTS5 and, if installed, the embedding runtime.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path


def run() -> int:
    if not os.environ.get("QT_QPA_PLATFORM") and sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import qasync
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        failures = loop.run_until_complete(_checks())
    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)
    print("self-test: " + ("FAILED" if failures else "ok"))
    return 1 if failures else 0


async def _checks() -> list[str]:
    failures: list[str] = []

    async def check(name: str, step: Callable[[], object]) -> None:
        try:
            result = step()
            if asyncio.iscoroutine(result):
                result = await result
            print(f"ok   {name}" + (f": {result}" if result else ""))
        except Exception as e:  # noqa: BLE001  (report every failing part, not just the first)
            failures.append(f"{name}: {e}")
            traceback.print_exc()

    from .td import TdJson

    def tdlib() -> str:
        lib = TdJson()
        lib.execute({"@type": "setLogVerbosityLevel", "new_verbosity_level": 0})
        parsed = lib.execute({"@type": "parseMarkdown", "text": {
            "@type": "formattedText", "text": "**ok**", "entities": []}})
        assert parsed and parsed.get("entities"), f"parseMarkdown returned {parsed}"
        version = lib.execute({"@type": "getOption", "name": "version"}) or {}
        return f"TDLib {version.get('value', '?')}"

    await check("libtdjson", tdlib)

    def baked() -> str:
        from .config import build_info
        info = build_info()  # report which values are baked in, never the values
        api = "Telegram API keys baked in" if info.get("TG_API_ID") and info.get(
            "TG_API_HASH") else "no Telegram API keys (reads .env)"
        return f"version {info.get('VERSION', 'dev')}, {api}, fields: {sorted(info)}"

    await check("build", baked)

    def fts5() -> str:
        db = sqlite3.connect(":memory:")
        db.execute("CREATE VIRTUAL TABLE t USING fts5(x, content='', contentless_delete=1)")
        return f"SQLite {sqlite3.sqlite_version}"

    await check("sqlite fts5", fts5)

    def icons() -> None:
        from PySide6.QtCore import QSize

        from .ui.icons import IconProvider
        image = IconProvider().requestImage("search/000000", QSize(), QSize(32, 32))
        assert not image.isNull(), "SVG rendering failed (QtSvg missing?)"

    await check("icons", icons)

    def semantic() -> str:
        from .services.embeddings import semantic_available
        if not semantic_available():
            return "not included (keyword search only)"
        import onnxruntime  # noqa: F401  (the part that fails on unsupported platforms)
        return "available"

    await check("search by meaning", semantic)

    def notifications() -> str:
        if sys.platform == "darwin":
            import UserNotifications  # noqa: F401
            return "UserNotifications"
        import jeepney  # noqa: F401
        return "D-Bus (jeepney)"

    await check("notifications", notifications)

    def media() -> str:
        import av
        import segno  # noqa: F401
        from rlottie_python import LottieAnimation  # noqa: F401

        for codec, mode in (("libopus", "w"), ("libvpx-vp9", "r")):
            av.codec.Codec(codec, mode)  # voice notes, video stickers with alpha
        return f"PyAV {av.__version__}, rlottie, segno"

    await check("media codecs", media)

    async def ui() -> str:
        from .app import Session, create_engine, dispose_engine
        from .config import Settings
        from .services.ai_store import AiStore
        from .services.search_index import SearchIndex
        from .ui.notifications import NullBackend
        from .ui.shell import ShellController

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(api_id=1, api_hash="self-test", data_dir=Path(tmp),
                                use_test_dc=True, td_log_level=0, log_level="WARNING")
            session = Session(settings, ai_store=AiStore(":memory:"),
                              search_index=SearchIndex(":memory:"),
                              notification_backend=NullBackend())
            warnings: list[str] = []
            engine = create_engine(session, ShellController(asyncio.Event()),
                                   on_warnings=warnings.extend)
            loaded = bool(engine.rootObjects())
            for window in engine.rootObjects():
                window.setProperty("visible", False)
            problems = list(warnings)
            dispose_engine(engine)
            await session.close()
        assert loaded, f"QML failed to load: {problems}"
        assert not problems, f"QML warnings: {problems}"
        return "QML loaded"

    await check("ui", ui)
    return failures
