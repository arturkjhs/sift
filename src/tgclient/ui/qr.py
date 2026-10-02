"""QR codes for QML (login by scanning with the phone): image://qr/<percent-encoded text>.

Dark modules on white with a quiet zone, whatever the theme: scanners need the contrast.
"""

from __future__ import annotations

from urllib.parse import unquote

import segno
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider

_QUIET = 2  # modules of white border
_DEFAULT_SIDE = 240


def matrix(text: str) -> list[list[bool]]:
    code = segno.make(text, error="m", micro=False)
    return [[bool(cell) for cell in row] for row in code.matrix]


def render(text: str, side: int) -> QImage:
    rows = matrix(text)
    count = len(rows) + 2 * _QUIET
    image = QImage(side, side, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    module = side / count
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#111111"))
    for y, row in enumerate(rows):
        for x, dark in enumerate(row):
            if dark:
                painter.drawRect(QRectF((x + _QUIET) * module, (y + _QUIET) * module,
                                        module + 0.5, module + 0.5))
    painter.end()
    return image


class QrProvider(QQuickImageProvider):
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, image_id: str, size: QSize, requested_size: QSize) -> QImage:
        text = unquote(image_id)
        if not text:
            return QImage()
        side = max(requested_size.width(), requested_size.height()) or _DEFAULT_SIDE
        return render(text, side)
