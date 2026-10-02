# Linux build environment: Ubuntu 22.04 (glibc 2.35) so the AppImage runs on Ubuntu 22.04+,
# Debian 12+, Fedora 36+. Python comes from uv (python-build-standalone, see
# packaging/setup_build_env.sh): shared libpython for PyInstaller and a recent SQLite (FTS5
# contentless_delete needs 3.43+; Ubuntu 22.04 has 3.37).
# aarch64 uses ubuntu:24.04 (glibc 2.39): PySide6 6.9 aarch64 wheels are manylinux_2_39.
ARG BASE=ubuntu:22.04
FROM ${BASE}
ENV DEBIAN_FRONTEND=noninteractive
# 24.04 renamed some libraries (t64 transition): pick whichever name exists.
RUN apt-get update && T64="$(apt-cache show libasound2t64 >/dev/null 2>&1 && echo t64)" \
    && apt-get install -y --no-install-recommends "libasound2$T64" \
       ca-certificates curl build-essential cmake gperf git perl zlib1g-dev file binutils \
       desktop-file-utils \
       libgl1 libegl1 libglib2.0-0 libfontconfig1 libfreetype6 libdbus-1-3 libxkbcommon0 \
       libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 \
       libxcb-xinerama0 libxcb-randr0 libxcb-render-util0 libxcb-image0 libwayland-client0 \
       libwayland-cursor0 libwayland-egl1 libpulse0 libnss3 libxrandr2 libxtst6 \
       libxcomposite1 libxdamage1 libxi6 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*
ARG UV_VERSION=0.9.5
RUN curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$(uname -m)-unknown-linux-gnu.tar.gz" \
    | tar -xz --strip-components=1 -C /usr/local/bin
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python UV_LINK_MODE=copy
RUN git config --global --add safe.directory '*'
WORKDIR /src
