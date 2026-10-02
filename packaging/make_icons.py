"""Render src/tgclient/ui/app-icon.svg to the formats each platform wants.

    python packaging/make_icons.py <out_dir>

Writes <out_dir>/png/<size>.png (16..1024) and, on macOS, <out_dir>/tgclient.icns.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)
SOURCE = Path(__file__).resolve().parents[1] / "src" / "tgclient" / "ui" / "app-icon.svg"


def render(renderer: QSvgRenderer, size: int, path: Path) -> None:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    if not image.save(str(path)):
        raise SystemExit(f"Could not write {path}")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/icons")
    _app = QGuiApplication([])
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        raise SystemExit(f"Invalid SVG: {SOURCE}")
    (out / "png").mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        render(renderer, size, out / "png" / f"{size}.png")
    shutil.copy(SOURCE, out / "tgclient.svg")

    if sys.platform == "darwin":
        iconset = out / "tgclient.iconset"
        iconset.mkdir(exist_ok=True)
        for size in (16, 32, 128, 256, 512):
            render(renderer, size, iconset / f"icon_{size}x{size}.png")
            render(renderer, size * 2, iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out / "tgclient.icns")],
                       check=True)
        shutil.rmtree(iconset)
    print(f"Icons written to {out}")


if __name__ == "__main__":
    main()
