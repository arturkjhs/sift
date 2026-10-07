# PyInstaller spec for tgclient: a one-folder app (macOS: tgclient.app).
#
# Inputs: vendor/tdlib or $TDLIB_DIR (scripts/build_tdlib.sh), build/icons (packaging/make_icons.py),
# src/tgclient/_build.py (written by the build scripts). Env: TGC_SEMANTIC=1 bundles
# fastembed/onnxruntime for search by meaning.
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT / "src"))
from tgclient.config import APP_ID, APP_NAME, build_info  # noqa: E402

ICONS = ROOT / "build" / "icons"
VERSION = build_info().get("VERSION", "0.1.0")
PLIST_VERSION = re.match(r"\d+(?:\.\d+){0,2}", VERSION).group(0)  # macOS wants digits only
LIB = "libtdjson.dylib" if sys.platform == "darwin" else "libtdjson.so"
TDLIB_DIR = Path(os.environ.get("TDLIB_DIR") or ROOT / "vendor" / "tdlib")
TDJSON = (TDLIB_DIR / "lib" / LIB).resolve()  # the real file, not the symlink

datas = collect_data_files("tgclient",
                           includes=["**/*.qml", "**/qmldir", "**/*.svg", "**/*.json"])
binaries = [(str(TDJSON), "tdlib")]
hiddenimports = collect_submodules("tgclient") + [
    "PySide6.QtSvg", "PySide6.QtMultimedia", "PySide6.QtQuickControls2",
]
# System notifications (ui/notifications.py imports them lazily, per platform).
NOTIFY_PACKAGES = (("objc", "Foundation", "CoreFoundation", "UserNotifications")
                   if sys.platform == "darwin" else ("jeepney",))
# M9: PyAV (its own FFmpeg with libopus/libvpx), rlottie (TGS), segno (QR codes).
NOTIFY_PACKAGES += ("av", "rlottie_python", "segno")
for package in NOTIFY_PACKAGES:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
if os.environ.get("TGC_SEMANTIC") == "1":
    for package in ("fastembed", "onnxruntime", "tokenizers", "py_rust_stemmers"):
        d, b, h = collect_all(package)
        datas += d
        binaries += b
        hiddenimports += h

# Qt ships far more than we use; their QML plugins would drag in e.g. QtWebEngine (~200 MB).
UNUSED_QT = (
    "QtWebEngine", "WebEngine", "Qt3D", "QtQuick3D", "Quick3D", "QtCharts", "QtGraphs",
    "QtDataVisualization", "QtLocation", "QtPdf", "QtVirtualKeyboard", "QtSensors",
    "QtScxml", "QtRemoteObjects", "QtWebView", "QtWebChannel", "QtTextToSpeech",
    "QtSpatialAudio", "QtBluetooth", "QtNfc", "QtSerialPort", "QtSerialBus", "QtHttpServer",
    "QtDesigner", "QtUiTools", "QtHelp", "QtTest", "QtQuickTest", "QtCanvasPainter",
    "Qt5Compat", "QtScxmlQml", "QtStateMachine", "QtLottie", "QtQuickTimeline",
    "QtQuickEffectMaker", "QtShaderTools", "QtWayland/Compositor", "QtWaylandCompositor",
    "QtQuick/Studio", "QtQuick/VirtualKeyboard", "Qt/labs/lottieqt",
)


def keep(entry) -> bool:
    dest, source = entry[0], entry[1]
    text = f"{dest}|{source}".replace("\\", "/")
    return not any(name in text for name in UNUSED_QT)


a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf"],
    noarchive=False,
)
a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=str(ICONS / "tgclient.icns") if sys.platform == "darwin" else None,
    codesign_identity=None,  # signed by build_macos.sh
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ICONS / "tgclient.icns"),
        bundle_identifier=APP_ID,
        version=PLIST_VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": PLIST_VERSION,
            "CFBundleVersion": PLIST_VERSION,
            "TgClientBuild": VERSION,
            "LSMinimumSystemVersion": os.environ.get("MACOSX_DEPLOYMENT_TARGET", "12.0"),
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # follow the system dark mode
            # Asked when the user records a voice message for the first time.
            "NSMicrophoneUsageDescription": "tgclient records voice messages when you press "
                                            "the microphone button.",
            # Asked when the user records a video message (a round one) for the first time.
            "NSCameraUsageDescription": "tgclient records video messages when you choose "
                                        "Video message.",
            "LSApplicationCategoryType": "public.app-category.social-networking",
        },
    )
