#!/usr/bin/env bash
# Builds TDLib (libtdjson) into vendor/tdlib for the current platform.
#
# Linux (Debian/Ubuntu) prerequisites:
#   sudo apt-get install make git zlib1g-dev libssl-dev gperf cmake g++
# macOS prerequisites: Homebrew (the script installs gperf, cmake, openssl@3).
#
# TDLib compilation needs several GB of RAM per job; lower JOBS if the build gets OOM-killed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TD_REF="${TD_REF:-master}"   # TODO: pin to a specific commit for reproducible builds
SRC="$ROOT/vendor/td"
PREFIX="$ROOT/vendor/tdlib"
JOBS="${JOBS:-$( (nproc || sysctl -n hw.ncpu) 2>/dev/null )}"

if [ ! -d "$SRC/.git" ]; then
  git clone https://github.com/tdlib/td.git "$SRC"
fi
git -C "$SRC" fetch --all --tags
git -C "$SRC" checkout "$TD_REF"
if [ "$TD_REF" = "master" ]; then
  git -C "$SRC" pull --ff-only
fi

CMAKE_ARGS=(-DCMAKE_BUILD_TYPE=Release "-DCMAKE_INSTALL_PREFIX:PATH=$PREFIX")

case "$(uname -s)" in
  Darwin)
    command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh" >&2; exit 1; }
    brew install gperf cmake openssl@3
    CMAKE_ARGS+=("-DOPENSSL_ROOT_DIR=$(brew --prefix openssl@3)")
    ;;
  Linux)
    for tool in cmake gperf g++ make; do
      command -v "$tool" >/dev/null || { echo "Missing $tool, see prerequisites at the top of this script" >&2; exit 1; }
    done
    ;;
  *)
    echo "Unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac

rm -rf "$SRC/build"
cmake -S "$SRC" -B "$SRC/build" "${CMAKE_ARGS[@]}"
cmake --build "$SRC/build" --target install -j "$JOBS"

echo
echo "Built libraries:"
ls -la "$PREFIX/lib" | grep -i tdjson
