"""Regenerate src/tgclient/store/emoji.json from Unicode's emoji-test.txt.

    python scripts/gen_emoji.py [version]

Emoji 15.0 by default: the newest that macOS 13.4+ (our minimum) renders, newer ones would show
as boxes. Skin-tone variants are left out (base emoji only). Data: Unicode License v3.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

VERSION = sys.argv[1] if len(sys.argv) > 1 else "15.0"
URL = f"https://www.unicode.org/Public/emoji/{VERSION}/emoji-test.txt"
OUT = Path(__file__).resolve().parents[1] / "src" / "tgclient" / "store" / "emoji.json"
SKIP_GROUPS = {"Component"}
TONES = re.compile("[\U0001F3FB-\U0001F3FF]")
LINE = re.compile(r"^[0-9A-F ]+;\s*fully-qualified\s*#\s*(\S+)\s+E[\d.]+\s+(.+)$")


def main() -> None:
    text = urllib.request.urlopen(URL, timeout=30).read().decode("utf-8")
    groups: list[list[object]] = []
    current: list[list[str]] | None = None
    for line in text.splitlines():
        if line.startswith("# group:"):
            name = line.split(":", 1)[1].strip()
            current = None if name in SKIP_GROUPS else []
            if current is not None:
                groups.append([name, current])
            continue
        match = LINE.match(line)
        if match and current is not None and not TONES.search(match.group(1)):
            current.append([match.group(1), match.group(2)])
    data = {"source": URL, "license": "Unicode License v3", "groups": groups}
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
    total = sum(len(g[1]) for g in groups)  # type: ignore[arg-type]
    print(f"{OUT.name}: {total} emoji in {len(groups)} groups, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
