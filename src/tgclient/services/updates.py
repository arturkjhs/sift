"""Updates from GitHub Releases. Qt-free.

The repository comes from the build (UPDATE_REPO in _build.py, CI bakes GITHUB_REPOSITORY) or
TGC_UPDATE_REPO; without one (development) there are no update checks. A check is one
anonymous GET to api.github.com. Installing:
- AppImage ($APPIMAGE set): the new file replaces the running one; it takes effect on restart.
- macOS: the .dmg is downloaded and opened; the user drags the app to Applications (no
  Developer ID yet, so no silent self-replacement).
- Flatpak and anything else: only a link to the release page.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

API = "https://api.github.com/repos/{repo}/releases/latest"


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    url: str  # release page
    notes: str
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.3.1' / '0.3.1-2-gabc' / '0.1.0.dev+abc' -> (0, 3, 1); dev builds sort below."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    if "dev" in current:
        return False  # local builds don't nag
    return parse_version(candidate) > parse_version(current)


def install_kind(environ: dict[str, str] | None = None, platform_name: str = sys.platform) -> str:
    """appimage | dmg | page"""
    env = os.environ if environ is None else environ
    if platform_name == "darwin":
        return "dmg"
    if env.get("APPIMAGE"):
        return "appimage"
    return "page"  # Flatpak (FLATPAK_ID), source installs, ...


def asset_suffix(kind: str, machine: str | None = None) -> str:
    arch = (machine or platform.machine()).lower()
    arch = {"amd64": "x86_64", "aarch64": "aarch64", "arm64": "arm64"}.get(arch, arch)
    if kind == "dmg":
        return f"-macos-{'arm64' if arch in ('arm64', 'aarch64') else 'x86_64'}.dmg"
    if kind == "appimage":
        return f"-linux-{'aarch64' if arch in ('arm64', 'aarch64') else 'x86_64'}.AppImage"
    return ""


class UpdateChecker:
    def __init__(self, repo: str, current: str, kind: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 machine: str | None = None) -> None:
        self.repo = repo
        self.current = current
        self.kind = kind or install_kind()
        self.machine = machine
        self._transport = transport

    def _client(self, timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=timeout,
                                 follow_redirects=True,
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "tgclient-updater"})

    async def latest(self) -> Release | None:
        """The newest release if it's newer than this build, else None."""
        try:
            async with self._client() as http:
                response = await http.get(API.format(repo=self.repo))
        except httpx.HTTPError as e:
            raise UpdateError(f"Network error: {e}") from e
        if response.status_code == 404:
            return None  # no releases yet
        if response.status_code >= 400:
            raise UpdateError(f"GitHub answered {response.status_code}")
        data: dict[str, Any] = response.json()
        tag = str(data.get("tag_name") or "")
        if data.get("draft") or data.get("prerelease") or not is_newer(tag, self.current):
            return None
        suffix = asset_suffix(self.kind, self.machine)
        asset = next((a for a in data.get("assets") or []
                      if suffix and str(a.get("name", "")).endswith(suffix)), None) or {}
        return Release(
            version=tag.lstrip("v"), url=str(data.get("html_url") or ""),
            notes=str(data.get("body") or "")[:4000],
            asset_name=str(asset.get("name") or ""),
            asset_url=str(asset.get("browser_download_url") or ""),
            asset_size=int(asset.get("size") or 0))

    async def download(self, release: Release, folder: Path,
                       progress: Callable[[float], None] = lambda fraction: None) -> Path:
        if not release.asset_url:
            raise UpdateError("No download for this system in the release")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / release.asset_name
        partial = target.with_name(target.name + ".part")
        try:
            async with self._client(timeout=600.0) as http, \
                    http.stream("GET", release.asset_url) as response:
                if response.status_code >= 400:
                    raise UpdateError(f"Download failed ({response.status_code})")
                total = int(response.headers.get("content-length") or release.asset_size or 0)
                done = 0
                f = await asyncio.to_thread(partial.open, "wb")  # no blocking I/O on the loop
                chunks = response.aiter_bytes(1 << 16)
                try:
                    async for chunk in chunks:
                        await asyncio.to_thread(f.write, chunk)
                        done += len(chunk)
                        if total:
                            progress(min(1.0, done / total))
                finally:
                    await chunks.aclose()  # see OpenRouter.stream()
                    await asyncio.to_thread(f.close)
        except httpx.HTTPError as e:
            partial.unlink(missing_ok=True)
            raise UpdateError(f"Network error: {e}") from e
        if release.asset_size and partial.stat().st_size != release.asset_size:
            partial.unlink(missing_ok=True)
            raise UpdateError("The download is incomplete")
        partial.replace(target)
        return target


def replace_appimage(new: Path, current: Path) -> None:
    """Swap the running AppImage for the new one (atomic rename on the same filesystem; the
    running process keeps its old inode)."""
    staged = current.with_name(current.name + ".new")
    staged.write_bytes(new.read_bytes())
    staged.chmod(0o755)
    staged.replace(current)
    new.unlink(missing_ok=True)
