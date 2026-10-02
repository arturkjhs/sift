#!/usr/bin/env bash
# Builds the Linux app: dist/tgclient-<version>-linux-<arch>.AppImage and
# dist/tgclient-linux-<arch>.tar.gz (the one-folder app, input for the Flatpak).
#
# Run inside packaging/linux/Dockerfile (see build_linux_docker.sh) for a portable build;
# running on the host also works but the glibc baseline is then the host's.
# Environment: as build_macos.sh (TG_API_ID, TG_API_HASH, TGC_SEMANTIC, PYTHON).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
ARCH="$(uname -m)"
APP_ID="io.github.tgclient.TgClient"
if [ -z "${TGC_SEMANTIC:-}" ]; then
  if "$PYTHON" -c "import fastembed" 2>/dev/null; then TGC_SEMANTIC=1; else TGC_SEMANTIC=0; fi
fi
export TGC_SEMANTIC

export TDLIB_DIR="${TDLIB_DIR:-$ROOT/vendor/tdlib}"
[ -f "$TDLIB_DIR/lib/libtdjson.so" ] || { echo "Run scripts/build_tdlib.sh first" >&2; exit 1; }
if ldd "$TDLIB_DIR/lib/libtdjson.so" | grep -qE "libssl|libcrypto"; then
  echo "libtdjson links a shared OpenSSL; rebuild with scripts/build_tdlib.sh" >&2
  exit 1
fi

trap 'rm -f "$ROOT/src/tgclient/_build.py"' EXIT
"$PYTHON" packaging/write_build_info.py
VERSION="$("$PYTHON" -c 'from tgclient.config import build_info; print(build_info()["VERSION"])')"
VERSION="${VERSION//+/-}"

"$PYTHON" packaging/make_icons.py build/icons
"$PYTHON" -m PyInstaller --noconfirm --clean --log-level WARN \
  --distpath dist --workpath build/pyinstaller packaging/tgclient.spec

echo "Self-test of the build:"
QT_QPA_PLATFORM=offscreen dist/tgclient/tgclient --self-test

tar -C dist -czf "dist/tgclient-linux-$ARCH.tar.gz" tgclient

# --- AppImage -----------------------------------------------------------------------------
APPDIR="build/AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a dist/tgclient "$APPDIR/usr/lib/tgclient"
cp "packaging/linux/$APP_ID.desktop" "$APPDIR/$APP_ID.desktop"
cp "packaging/linux/$APP_ID.desktop" "$APPDIR/usr/share/applications/"
cp build/icons/png/256.png "$APPDIR/$APP_ID.png"
cp build/icons/png/256.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP_ID.png"
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/tgclient/tgclient" "$@"
RUN
chmod +x "$APPDIR/AppRun"
desktop-file-validate "$APPDIR/$APP_ID.desktop"

TOOL="build/appimagetool-$ARCH.AppImage"
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
  chmod +x "$TOOL"
fi
OUT="dist/tgclient-$VERSION-linux-$ARCH.AppImage"
# No FUSE in containers: let the tool unpack itself.
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" "$TOOL" --no-appstream "$APPDIR" "$OUT"
echo
echo "Built $OUT ($(du -h "$OUT" | cut -f1)) and dist/tgclient-linux-$ARCH.tar.gz"
