#!/usr/bin/env bash
# Builds dist/tgclient-<version>-macos-<arch>.dmg for the current architecture.
#
# Prerequisites: scripts/build_tdlib.sh has been run (static OpenSSL), and PYTHON is a
# *portable* Python with `pip install -e ".[package]"` (plus ".[semantic]" for search by
# meaning). Not Homebrew's Python: it is built for the build machine's macOS only. Use
#   uv venv build/venv --python 3.12 --managed-python && uv pip install -p build/venv ...
# (python-build-standalone: macOS 11+, recent SQLite with FTS5).
#
# Environment:
#   TG_API_ID, TG_API_HASH   baked into the app (release builds: TGC_REQUIRE_API=1)
#   CODESIGN_IDENTITY        "Developer ID Application: ..." to sign with a certificate;
#                            default "-" (ad-hoc). No notarization: see packaging/README.md.
#   TGC_SEMANTIC=1           bundle fastembed/onnxruntime (default: if fastembed is installed)
#   PYTHON                   python with the build deps (default: .venv/bin/python)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/build/venv/bin/python}"
ARCH="$(uname -m)"
IDENTITY="${CODESIGN_IDENTITY:--}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-12.0}"
if [ -z "${TGC_SEMANTIC:-}" ]; then
  if "$PYTHON" -c "import fastembed" 2>/dev/null; then TGC_SEMANTIC=1; else TGC_SEMANTIC=0; fi
fi
export TGC_SEMANTIC

export TDLIB_DIR="${TDLIB_DIR:-$ROOT/vendor/tdlib}"
[ -f "$TDLIB_DIR/lib/libtdjson.dylib" ] || { echo "Run scripts/build_tdlib.sh first" >&2; exit 1; }
if otool -L "$TDLIB_DIR/lib/libtdjson.dylib" | grep -qE "/(opt/homebrew|usr/local)/"; then
  echo "libtdjson links Homebrew libraries; rebuild with scripts/build_tdlib.sh" >&2
  exit 1
fi

trap 'rm -f "$ROOT/src/tgclient/_build.py"' EXIT
"$PYTHON" packaging/write_build_info.py
VERSION="$("$PYTHON" -c 'from tgclient.config import build_info; print(build_info()["VERSION"])')"

"$PYTHON" packaging/make_icons.py build/icons
"$PYTHON" -m PyInstaller --noconfirm --clean --log-level WARN \
  --distpath dist --workpath build/pyinstaller packaging/tgclient.spec

APP="dist/tgclient.app"
# The bundle runs on the newest macOS any of its binaries was built for: record it, and refuse
# builds that would exclude too many Macs (e.g. made with a Homebrew Python built for the
# build machine's own macOS).
MINOS="$("$PYTHON" packaging/macos_minos.py "$APP" --max "${TGC_MAX_MACOS:-13.4}")"
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion $MINOS" "$APP/Contents/Info.plist"
echo "Minimum macOS: $MINOS"

# Ad-hoc (or Developer ID) signature over the whole bundle; required on Apple Silicon.
codesign --force --deep --timestamp=none --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"

echo "Self-test of the bundle:"
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/tgclient" --self-test

DMG="dist/tgclient-${VERSION//+/-}-macos-$ARCH.dmg"
STAGING="build/dmg"
rm -rf "$STAGING" "$DMG"
mkdir -p "$STAGING"
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -quiet -volname "tgclient" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
rm -rf "$STAGING"
echo
echo "Built $DMG ($(du -h "$DMG" | cut -f1)), app $(du -sh "$APP" | cut -f1)"
