"""Build the UI translations: QML strings (pyside6-lupdate) + src/tgclient/ui/i18n/<lang>.json
(source text -> translation) -> src/tgclient/ui/i18n/tgclient_<lang>.qm (pyside6-lrelease).

Run after changing qsTr() strings or the JSON files:  uv run python scripts/update_translations.py
Lists strings that have no translation yet (they show in English).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QML = ROOT / "src" / "tgclient" / "ui" / "qml" / "TgClient"
I18N = ROOT / "src" / "tgclient" / "ui" / "i18n"
LANGUAGES = ("ru", "uk", "cs")


def tool(name: str) -> str:
    found = shutil.which(name) or str(Path(sys.executable).parent / name)
    if not Path(found).exists():
        sys.exit(f"{name} not found (it comes with PySide6)")
    return found


def main() -> int:
    missing_total = 0
    with tempfile.TemporaryDirectory() as tmp:
        for lang in LANGUAGES:
            ts = Path(tmp) / f"tgclient_{lang}.ts"
            subprocess.run([tool("pyside6-lupdate"), *map(str, sorted(QML.glob("*.qml"))),
                            "-ts", str(ts), "-silent"], check=True)
            strings = json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))
            tree = ET.parse(ts)
            tree.getroot().set("language", lang)
            missing = set()
            for message in tree.iter("message"):
                source = message.findtext("source") or ""
                translation = message.find("translation")
                assert translation is not None
                if source in strings:
                    translation.text = strings[source]
                    translation.attrib.pop("type", None)
                else:
                    missing.add(source)
            tree.write(ts, encoding="utf-8", xml_declaration=True)
            subprocess.run([tool("pyside6-lrelease"), str(ts), "-qm",
                            str(I18N / f"tgclient_{lang}.qm"), "-silent"], check=True)
            for source in sorted(missing):
                print(f"{lang}: not translated: {source!r}")
            missing_total += len(missing)
    return 1 if missing_total else 0


if __name__ == "__main__":
    sys.exit(main())
