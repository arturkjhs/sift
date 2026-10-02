#!/usr/bin/env bash
# Creates build/venv: a portable Python (python-build-standalone via uv: shared libpython for
# PyInstaller, SQLite with FTS5 contentless_delete) with release-pinned dependencies.
# On macOS, wheels are resolved for MACOSX_DEPLOYMENT_TARGET, not for the build machine.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
EXTRAS="${TGC_EXTRAS:-package,semantic}"

VENV="${TGC_VENV:-build/venv}"
uv python install 3.12
rm -rf "$VENV"
uv venv "$VENV" --python "$(uv python find 3.12 --managed-python)"
args=(-p "$VENV/bin/python" -c packaging/constraints.txt -e ".[$EXTRAS]")
if [ "$(uname -s)" = "Darwin" ]; then
  export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.0}"
  args+=(--python-platform "$(uname -m | sed 's/arm64/aarch64/')-apple-darwin")
fi
uv pip install "${args[@]}"
"$VENV/bin/python" -c "import sqlite3, PySide6; print('python ok: SQLite', sqlite3.sqlite_version, 'PySide6', PySide6.__version__)"
