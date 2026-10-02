#!/usr/bin/env bash
# Builds TDLib (libtdjson) into vendor/tdlib for the current platform and architecture.
#
# OpenSSL is built from source and linked statically, so libtdjson has no dependency on
# Homebrew or a particular distro's libssl (needed for .dmg/AppImage/Flatpak, harmless for dev).
#
# Linux (Debian/Ubuntu) prerequisites:
#   sudo apt-get install make git zlib1g-dev gperf cmake g++ perl curl
# macOS prerequisites: Xcode command line tools, Homebrew (the script installs gperf, cmake).
#
# Environment:
#   TD_REF        TDLib commit to build (pinned below; "master" for the latest)
#   PREFIX        install dir (default vendor/tdlib)
#   JOBS          parallel jobs; TDLib needs several GB of RAM per job, lower it on OOM
#   MACOSX_DEPLOYMENT_TARGET  oldest macOS to support (default 12.0, same as Qt 6.9+)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Pinned for reproducible builds. TDLib's master changes its API (e.g. inputMessagePhoto):
# bump deliberately and run the tests.
TD_REF="${TD_REF:-42e6a5259551178d1dab54a22ad96d14bd906e20}"
OPENSSL_VERSION="3.5.9"
OPENSSL_SHA256="603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a"
SRC="$ROOT/vendor/td"
PREFIX="${PREFIX:-$ROOT/vendor/tdlib}"
DEPS="$ROOT/vendor/deps/$(uname -s)-$(uname -m)"
JOBS="${JOBS:-$( (nproc || sysctl -n hw.ncpu) 2>/dev/null )}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-12.0}"

case "$(uname -s)" in
  Darwin)
    command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh" >&2; exit 1; }
    brew list gperf >/dev/null 2>&1 || brew install gperf
    command -v cmake >/dev/null || brew install cmake
    OPENSSL_TARGET="darwin64-$(uname -m)-cc"
    OPENSSL_FLAGS=("-mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET")
    ;;
  Linux)
    for tool in cmake gperf g++ make perl curl; do
      command -v "$tool" >/dev/null || { echo "Missing $tool, see prerequisites at the top of this script" >&2; exit 1; }
    done
    OPENSSL_TARGET="linux-$(uname -m)"
    OPENSSL_FLAGS=("-fPIC")
    ;;
  *)
    echo "Unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac

# --- OpenSSL (static) ---------------------------------------------------------------------
OPENSSL_PREFIX="$DEPS/openssl-$OPENSSL_VERSION"
if [ ! -f "$OPENSSL_PREFIX/lib/libssl.a" ]; then
  mkdir -p "$DEPS"
  archive="$DEPS/openssl-$OPENSSL_VERSION.tar.gz"
  curl -fsSL -o "$archive" \
    "https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VERSION/openssl-$OPENSSL_VERSION.tar.gz"
  if command -v sha256sum >/dev/null; then
    actual="$(sha256sum "$archive" | cut -d' ' -f1)"
  else
    actual="$(shasum -a 256 "$archive" | cut -d' ' -f1)"
  fi
  [ "$actual" = "$OPENSSL_SHA256" ] || { echo "OpenSSL checksum mismatch: $actual" >&2; exit 1; }
  rm -rf "$DEPS/openssl-src"
  mkdir -p "$DEPS/openssl-src"
  tar -xzf "$archive" -C "$DEPS/openssl-src" --strip-components=1
  (
    cd "$DEPS/openssl-src"
    ./Configure "$OPENSSL_TARGET" no-shared no-tests no-docs no-apps \
      --prefix="$OPENSSL_PREFIX" --libdir=lib "${OPENSSL_FLAGS[@]}"
    make -j "$JOBS" >/dev/null
    make install_sw >/dev/null
  )
fi

# --- TDLib --------------------------------------------------------------------------------
if [ ! -d "$SRC/.git" ]; then
  git clone https://github.com/tdlib/td.git "$SRC"
fi
if ! git -C "$SRC" cat-file -e "$TD_REF^{commit}" 2>/dev/null || [ "$TD_REF" = "master" ]; then
  git -C "$SRC" fetch --all --tags
fi
git -C "$SRC" checkout --quiet "$TD_REF"
if [ "$TD_REF" = "master" ]; then
  git -C "$SRC" pull --ff-only
fi

CMAKE_ARGS=(
  -DCMAKE_BUILD_TYPE=Release
  "-DCMAKE_INSTALL_PREFIX:PATH=$PREFIX"
  "-DOPENSSL_ROOT_DIR=$OPENSSL_PREFIX"
  -DOPENSSL_USE_STATIC_LIBS=ON
)
if [ "$(uname -s)" = "Darwin" ]; then
  CMAKE_ARGS+=("-DCMAKE_OSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET")
fi

BUILD="$SRC/build-$(uname -s)-$(uname -m)"
rm -rf "$BUILD"
cmake -S "$SRC" -B "$BUILD" "${CMAKE_ARGS[@]}"
cmake --build "$BUILD" --target install -j "$JOBS"

echo
echo "Built libraries:"
ls -la "$PREFIX"/lib/libtdjson*
if [ "$(uname -s)" = "Darwin" ]; then
  otool -L "$PREFIX/lib/libtdjson.dylib"
else
  ldd "$PREFIX/lib/libtdjson.so" || true
fi
