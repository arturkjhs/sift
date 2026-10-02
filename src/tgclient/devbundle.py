"""macOS development runs (`uv run tgclient`) inside a small app bundle of our own.

macOS attributes the Dock icon, notification permission and notifications to the main bundle
of the process. A plain `python` has none, and a Homebrew (framework) Python lives inside
Python.app, so notifications would come from "Python" with its icon. Here we build
<cache>/dev/tgclient.app once (bundle id APP_ID + ".dev", our icon, a copy of the Python
executable, ad-hoc signed) and re-exec from it. `__PYVENV_LAUNCHER__` keeps the venv.

Packaged builds are real bundles already; TGC_NO_DEV_BUNDLE=1 turns this off.
"""

from __future__ import annotations

import json
import logging
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from platformdirs import user_cache_dir

from .config import APP_ID, APP_NAME

log = logging.getLogger(__name__)

DEV_BUNDLE_ID = APP_ID + ".dev"
_MARKER = "TGC_DEV_BUNDLE"
_ICON_SVG = Path(__file__).resolve().parent / "ui" / "app-icon.svg"


def relaunch_in_dev_bundle(argv: list[str]) -> None:
    """Replace this process with one started from the dev bundle; returns if not applicable."""
    if not _applicable():
        return
    try:
        executable = ensure_bundle(Path(user_cache_dir(APP_NAME, appauthor=False)) / "dev")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.warning("Dev app bundle unavailable, notifications fall back to osascript: %s", e)
        return
    env = {**os.environ, _MARKER: "1", "__PYVENV_LAUNCHER__": sys.executable}
    os.execve(executable, [str(executable), "-c", "from tgclient.app import main; main()",
                           *argv[1:]], env)


def _applicable() -> bool:
    return (sys.platform == "darwin" and not getattr(sys, "frozen", False)
            and not os.environ.get(_MARKER) and not os.environ.get("TGC_NO_DEV_BUNDLE")
            and os.environ.get("QT_QPA_PLATFORM") != "offscreen")


def python_binary() -> Path:
    """The real interpreter binary (not the framework's bin/ stub that re-execs Python.app)."""
    if getattr(sys, "_framework", ""):
        return Path(sys.base_prefix) / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    return Path(os.path.realpath(getattr(sys, "_base_executable", sys.executable)))


def ensure_bundle(root: Path) -> Path:
    """Create or refresh <root>/tgclient.app; returns its executable."""
    source = python_binary()
    app = root / f"{APP_NAME}.app"
    executable = app / "Contents" / "MacOS" / APP_NAME
    stamp = json.dumps({"python": str(source), "mtime": source.stat().st_mtime,
                        "icon": _ICON_SVG.stat().st_mtime, "version": 2})
    stamp_file = root / "stamp.json"
    if executable.exists() and stamp_file.exists() and stamp_file.read_text() == stamp:
        return executable

    root.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(dir=root)) / app.name
    contents = build / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    shutil.copy2(source, contents / "MacOS" / APP_NAME)
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump({
            "CFBundleIdentifier": DEV_BUNDLE_ID,
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleExecutable": APP_NAME,
            "CFBundlePackageType": "APPL",
            "CFBundleIconFile": APP_NAME,
            "CFBundleShortVersionString": "0.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSMicrophoneUsageDescription": "tgclient records voice messages when you press "
                                            "the microphone button.",
        }, f)
    write_icns(contents / "Resources" / f"{APP_NAME}.icns")
    subprocess.run(["codesign", "--force", "--sign", "-", str(build)], check=True,
                   capture_output=True)
    if app.exists():
        shutil.rmtree(app)
    build.rename(app)
    shutil.rmtree(build.parent, ignore_errors=True)
    stamp_file.write_text(stamp)
    return executable


def write_icns(path: Path) -> None:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(_ICON_SVG))
    if not renderer.isValid():
        raise ValueError(f"invalid icon {_ICON_SVG}")
    iconset = Path(tempfile.mkdtemp()) / "icon.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale, suffix in ((1, ""), (2, "@2x")):
            side = size * scale
            image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter, QRectF(0, 0, side, side))
            painter.end()
            image.save(str(iconset / f"icon_{size}x{size}{suffix}.png"))
    try:
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True,
                       capture_output=True)
    finally:
        shutil.rmtree(iconset.parent, ignore_errors=True)
