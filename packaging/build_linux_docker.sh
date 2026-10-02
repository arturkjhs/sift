#!/usr/bin/env bash
# Portable Linux build in the Ubuntu 22.04 container (same as CI). Builds TDLib on first run
# (cached in vendor/, separately per OS/arch), then the AppImage and tar.gz into dist/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="$(docker info --format '{{.Architecture}}')"
# glibc baseline: x86_64 2.35 (Ubuntu 22.04); aarch64 2.39, the oldest PySide6 6.9 supports there.
BASE="ubuntu:22.04"
[ "$ARCH" = "aarch64" ] && BASE="ubuntu:24.04"
IMAGE="tgclient-linux-build:${BASE#ubuntu:}"
docker build -t "$IMAGE" --build-arg "BASE=$BASE" "$ROOT/packaging/linux"
docker run --rm -v "$ROOT:/src" \
  -e TG_API_ID -e TG_API_HASH -e TGC_REQUIRE_API -e TGC_VERSION -e TGC_SEMANTIC \
  -e JOBS="${JOBS:-2}" "$IMAGE" bash -euo pipefail -c '
    # The host checkout may hold a macOS build in vendor/tdlib: keep Linux builds apart.
    export PREFIX=/src/vendor/tdlib-linux-$(uname -m)
    [ -f "$PREFIX/lib/libtdjson.so" ] || ./scripts/build_tdlib.sh
    TGC_VENV=/tmp/venv ./packaging/setup_build_env.sh
    TDLIB_DIR="$PREFIX" PYTHON=/tmp/venv/bin/python ./packaging/build_linux.sh
  '
