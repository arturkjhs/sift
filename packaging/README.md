# Packaging

| Target | Script | Output | Runs on |
|---|---|---|---|
| macOS arm64 / x86_64 | `build_macos.sh` | `dist/tgclient-<ver>-macos-<arch>.dmg` | macOS 13.4+ |
| Linux AppImage | `build_linux_docker.sh` | `dist/tgclient-<ver>-linux-<arch>.AppImage` | x86_64: glibc 2.35+ (Ubuntu 22.04, Debian 12, Fedora 36); aarch64: glibc 2.39+ (PySide6 wheels) |
| Linux Flatpak | `flatpak/io.github.tgclient.TgClient.yml` | `.flatpak` bundle | any distro with Flatpak |

CI (`.github/workflows/build.yml`) builds all of them on every push; a `v*` tag creates a draft
GitHub release with the files. Repository secrets `TG_API_ID` and `TG_API_HASH`
(my.telegram.org) are baked into the builds; without them a build reads `.env` like a dev run.

## How it fits together

- `scripts/build_tdlib.sh` builds libtdjson at a pinned TDLib commit with OpenSSL built from
  source and linked statically (no Homebrew or distro libssl at runtime).
- `setup_build_env.sh` makes `build/venv` from python-build-standalone (via uv): portable,
  shared libpython for PyInstaller, SQLite with FTS5 `contentless_delete`. Versions come from
  `constraints.txt` (PySide6 6.9.3, onnxruntime 1.23.2; see the comments there for why).
  On macOS wheels are picked for `MACOSX_DEPLOYMENT_TARGET`, not for the build machine.
- `tgclient.spec` (PyInstaller) makes a one-folder app, drops unused Qt modules, bundles
  libtdjson and, with `TGC_SEMANTIC=1`, fastembed + onnxruntime.
- Every build runs `tgclient --self-test` from inside the package: libtdjson, QML, icons,
  QtMultimedia, SQLite FTS5, the embedding runtime.
- macOS: `macos_minos.py` scans all binaries for the newest `minos` and writes it into
  `LSMinimumSystemVersion`; the build fails above 13.4 (`TGC_MAX_MACOS`).
- The Flatpak wraps the same tested one-folder build in the sandbox.

## Local builds

```bash
./scripts/build_tdlib.sh             # once per TDLib bump (~15 min)
./packaging/setup_build_env.sh       # build/venv with pinned versions
./packaging/build_macos.sh           # -> dist/*.dmg

./packaging/build_linux_docker.sh    # Linux in the Ubuntu 22.04 container -> dist/*.AppImage
```

## macOS signing

Builds are signed ad-hoc (no paid Apple Developer membership right now), so Gatekeeper blocks
the first launch of a downloaded copy. Tell users:

1. Open the `.dmg`, drag tgclient to Applications.
2. Open it once (it gets blocked), then System Settings → Privacy & Security →
   "tgclient was blocked" → **Open Anyway**.
   Or in Terminal: `xattr -dr com.apple.quarantine /Applications/tgclient.app`.

With an active membership: set `CODESIGN_IDENTITY="Developer ID Application: …"` and add
notarization (`xcrun notarytool submit … --wait`, `xcrun stapler staple`); that also needs the
hardened runtime and entitlements for Python. Don't hand out builds signed with someone else's
Team ID: a later Team ID change resets users' permissions and Keychain access.

## What is baked into a build

`VERSION`, `TG_API_ID`, `TG_API_HASH` and `UPDATE_REPO` (from the environment or the repo's `.env`;
`tgclient --self-test` lists the baked fields, never their values). These identify the app,
not a user, and anyone can extract them from the binary: use a dedicated Telegram account
for them, so a ban for someone else's abuse doesn't hit your personal one.

The OpenRouter key is never baked in: each user adds their own in Settings → AI. It's checked
with OpenRouter (`GET /api/v1/key`, free) and saved, readable only by the user (0600), in
`~/Library/Application Support/tgclient/.env` (macOS), `~/.config/tgclient/.env` (Linux) or
`~/.var/app/io.github.tgclient.TgClient/config/tgclient/.env` (Flatpak). An
`OPENROUTER_API_KEY` in the environment takes precedence at startup.

## Updates

A build with `UPDATE_REPO` (CI bakes `GITHUB_REPOSITORY`; locally set `TGC_UPDATE_REPO`) checks
`https://api.github.com/repos/<repo>/releases/latest` 30 s after start and then daily (Settings →
Updates can turn it off). It picks the asset by name: `…-macos-<arm64|x86_64>.dmg` or
`…-linux-<x86_64|aarch64>.AppImage`, so keep these names. The AppImage replaces itself
(`$APPIMAGE`) and asks for a restart; on macOS the `.dmg` is downloaded and opened (ad-hoc
signing, no silent self-replacement); Flatpak and source installs only get the release link.
Drafts and pre-releases are ignored, and `dev` builds never report updates.

## Media dependencies (M9)

PyAV brings its own FFmpeg with libopus (Qt's FFmpeg can't encode Opus, which Telegram voice
notes need) and libvpx (video stickers keep their alpha only with libvpx-vp9). PyAV 15.1 is
pinned: newer macOS arm64 wheels need macOS 14. rlottie-python renders TGS stickers, segno
draws login QR codes. `NSMicrophoneUsageDescription` in Info.plist is required for recording
on macOS; the Flatpak's `--socket=pulseaudio` covers the microphone on Linux.
`tgclient --self-test` checks the codecs ("media codecs").
