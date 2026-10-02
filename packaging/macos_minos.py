"""Find the oldest macOS a bundle can run on: the highest `minos` of all its Mach-O files.

    python packaging/macos_minos.py dist/tgclient.app [--max 14.0]

Prints that version. With --max, fails and lists the offenders if anything needs a newer
macOS (e.g. a Python built by Homebrew for the build machine's own macOS).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MACHO_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def minos(path: Path) -> tuple[int, ...] | None:
    out = subprocess.run(["vtool", "-show-build", str(path)], capture_output=True, text=True,
                         check=False).stdout  # some stubs have no build version: just skip
    versions = []
    command = ""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        if parts[0] == "cmd":
            command = parts[1]
        # LC_BUILD_VERSION: "minos"; its "version" lines are linker versions.
        # Older LC_VERSION_MIN_MACOSX: "version" is the minimum macOS.
        elif parts[0] == "minos" or (parts[0] == "version"
                                     and command == "LC_VERSION_MIN_MACOSX"):
            versions.append(tuple(int(x) for x in parts[1].split(".")))
    return max(versions) if versions else None


def is_macho(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) in MACHO_MAGICS
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--max", help="fail if any binary needs a newer macOS than this")
    args = parser.parse_args()
    found: dict[Path, tuple[int, ...]] = {}
    for path in args.bundle.rglob("*"):
        if path.is_file() and not path.is_symlink() and is_macho(path):
            version = minos(path)
            if version:
                found[path] = version
    highest = max(found.values())
    print(".".join(map(str, highest[:2])))
    if args.max:
        limit = tuple(int(x) for x in args.max.split("."))
        offenders = sorted((v, p) for p, v in found.items() if v > limit)
        if offenders:
            for version, path in offenders[-15:]:
                print(f"  needs macOS {'.'.join(map(str, version))}: {path}", file=sys.stderr)
            sys.exit(f"{len(offenders)} binaries need a macOS newer than {args.max}")


if __name__ == "__main__":
    main()
