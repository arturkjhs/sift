"""Thin ctypes binding to TDLib's JSON interface (libtdjson).

Only this module touches the native library. Everything else goes through TdHub/TdClient.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import sys
from pathlib import Path
from typing import Any


class TdJsonNotFound(RuntimeError):
    pass


def _library_candidates() -> list[str]:
    env = os.environ.get("TDJSON_PATH")
    if env:
        return [env]

    name = "libtdjson.dylib" if sys.platform == "darwin" else "libtdjson.so"
    candidates = []
    bundle = getattr(sys, "_MEIPASS", None)  # packaged app (PyInstaller): shipped next to Qt
    if bundle:  # versioned file name, e.g. libtdjson.1.8.67.dylib / libtdjson.so.1.8.67
        candidates += sorted(str(p) for p in (Path(bundle) / "tdlib").glob("libtdjson*"))
    # src/tgclient/td/tdjson.py -> repo root is three levels above the package dir
    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(str(repo_root / "vendor" / "tdlib" / "lib" / name))

    found = ctypes.util.find_library("tdjson")
    if found:
        candidates.append(found)
    candidates.append(name)
    return candidates


class TdJson:
    """Wrapper around the td_* C functions. Requests and responses are Python dicts."""

    def __init__(self, path: str | None = None) -> None:
        self._lib = self._load(path)
        lib = self._lib

        lib.td_create_client_id.restype = ctypes.c_int
        lib.td_create_client_id.argtypes = []

        lib.td_send.restype = None
        lib.td_send.argtypes = [ctypes.c_int, ctypes.c_char_p]

        lib.td_receive.restype = ctypes.c_char_p
        lib.td_receive.argtypes = [ctypes.c_double]

        lib.td_execute.restype = ctypes.c_char_p
        lib.td_execute.argtypes = [ctypes.c_char_p]

    @staticmethod
    def _load(path: str | None) -> ctypes.CDLL:
        errors = []
        for candidate in [path] if path else _library_candidates():
            try:
                return ctypes.CDLL(candidate)
            except OSError as e:
                errors.append(f"  {candidate}: {e}")
        raise TdJsonNotFound(
            "libtdjson not found. Run scripts/build_tdlib.sh or set TDJSON_PATH.\nTried:\n"
            + "\n".join(errors)
        )

    def create_client_id(self) -> int:
        return self._lib.td_create_client_id()

    def send(self, client_id: int, request: dict[str, Any]) -> None:
        self._lib.td_send(client_id, json.dumps(request).encode())

    def receive(self, timeout: float) -> dict[str, Any] | None:
        """Blocks up to `timeout` seconds. Must only be called from a single thread."""
        raw = self._lib.td_receive(timeout)  # ctypes copies the C string into bytes
        return json.loads(raw) if raw else None

    def execute(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Synchronous call for the few methods TDLib allows (logging, parsing, etc.)."""
        raw = self._lib.td_execute(json.dumps(request).encode())
        return json.loads(raw) if raw else None
