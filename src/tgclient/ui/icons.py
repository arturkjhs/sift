"""The app's own line icons, rendered in any color by a QML image provider.

    image://icon/<name>/<rrggbb>

Drawn for this project on a 24x24 grid (stroke 1.8, round caps), not taken from any icon set.
Coloring happens here, not with shader effects, so it works in software rendering and tests.
Set `sourceSize` in QML (physical pixels) to get crisp output.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

ICONS: dict[str, str] = {
    "reply": '<path d="M9 6 3.5 11.5 9 17M3.5 11.5H14a6.5 6.5 0 0 1 6.5 6.5v1"/>',
    "copy": '<rect x="8.5" y="8.5" width="12" height="12" rx="2.5"/>'
            '<path d="M15.5 8.5V6a2.5 2.5 0 0 0-2.5-2.5H6A2.5 2.5 0 0 0 3.5 6v7'
            'A2.5 2.5 0 0 0 6 15.5h2.5"/>',
    "folder": '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13'
              'a2 2 0 0 1-2-2z"/>',
    "sparkle": '<path d="M11 3.5 12.9 9 18.5 11l-5.6 2-1.9 5.5L9.1 13 3.5 11l5.6-2z"/>'
               '<path d="M18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
    "person": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 20.5 20.5"/>',
    "transcript": '<path d="M4 6.5h16M4 12h16M4 17.5h9.5"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 12h3M11 12h9M4 17h11M19 17h1"/>'
                '<circle cx="15" cy="7" r="2"/><circle cx="9" cy="12" r="2"/>'
                '<circle cx="17" cy="17" r="2"/>',
    "refresh": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4v4.5H15"/>',
    "chevron-down": '<path d="M6.5 9.5 12 15l5.5-5.5"/>',
    "arrow-down": '<path d="M12 4.5v15M6 13.5l6 6 6-6"/>',
    "summary": '<path d="M5 5.5h14M5 10h14M5 14.5h8"/><path d="M16.5 14.5l1 2.5 2.5 1-2.5 1'
               '-1 2.5-1-2.5-2.5-1 2.5-1z"/>',
    "emoji": '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 14.2a4.2 4.2 0 0 0 7 0"/>'
             '<path d="M9.2 9.6v.6M14.8 9.6v.6"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "sticker": '<path d="M20.5 12.5V7a3.5 3.5 0 0 0-3.5-3.5H7A3.5 3.5 0 0 0 3.5 7v10'
               'A3.5 3.5 0 0 0 7 20.5h5.5z"/><path d="M12.5 20.5v-4.5a3.5 3.5 0 0 1 3.5-3.5h4.5"/>',
    "open": '<path d="M13.5 4.5h6v6M19.5 4.5 11 13M17.5 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8.5'
            'a2 2 0 0 1 2-2h4"/>',
}

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#{color}" '
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)
_DEFAULT_SIDE = 48


def svg(name: str, color: str) -> bytes:
    return _TEMPLATE.format(color=color, body=ICONS[name]).encode()


class IconProvider(QQuickImageProvider):
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, image_id: str, size: QSize, requested_size: QSize) -> QImage:
        name, _, color = image_id.partition("/")
        color = color.lstrip("#")[-6:] or "000000"
        if name not in ICONS or not all(c in "0123456789abcdefABCDEF" for c in color):
            return QImage()
        width = requested_size.width() if requested_size.width() > 0 else _DEFAULT_SIDE
        height = requested_size.height() if requested_size.height() > 0 else width
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(QByteArray(svg(name, color))).render(painter, QRectF(0, 0, width, height))
        painter.end()
        return image
