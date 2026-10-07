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
    "back": '<path d="M14.5 6 8.5 12l6 6"/>',
    "arrow-down": '<path d="M12 4.5v15M6 13.5l6 6 6-6"/>',
    "summary": '<path d="M5 5.5h14M5 10h14M5 14.5h8"/><path d="M16.5 14.5l1 2.5 2.5 1-2.5 1'
               '-1 2.5-1-2.5-2.5-1 2.5-1z"/>',
    "emoji": '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 14.2a4.2 4.2 0 0 0 7 0"/>'
             '<path d="M9.2 9.6v.6M14.8 9.6v.6"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "sticker": '<path d="M20.5 12.5V7a3.5 3.5 0 0 0-3.5-3.5H7A3.5 3.5 0 0 0 3.5 7v10'
               'A3.5 3.5 0 0 0 7 20.5h5.5z"/><path d="M12.5 20.5v-4.5a3.5 3.5 0 0 1 3.5-3.5h4.5"/>',
    "edit": '<path d="M4.5 19.5h4L19 9a2.8 2.8 0 0 0-4-4L4.5 15.5z"/><path d="M13.5 6.5l4 4"/>',
    "trash": '<path d="M4.5 7h15M9.5 7V5a1.5 1.5 0 0 1 1.5-1.5h2A1.5 1.5 0 0 1 14.5 5v2"/>'
             '<path d="M6.5 7l.9 11.6A2 2 0 0 0 9.4 20.5h5.2a2 2 0 0 0 2-1.9L17.5 7"/>'
             '<path d="M10.2 11v5.5M13.8 11v5.5"/>',
    "forward": '<path d="M15 6l5.5 5.5L15 17M20.5 11.5H10a6.5 6.5 0 0 0-6.5 6.5v1"/>',
    "attach": '<path d="M19.5 11.5l-7.2 7.2a4.6 4.6 0 0 1-6.5-6.5l7.6-7.6a3.1 3.1 0 0 1 4.4 4.4'
              'l-7.5 7.5a1.5 1.5 0 0 1-2.2-2.2l6.9-6.9"/>',
    "file": '<path d="M13.5 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8.5z"/>'
            '<path d="M13.5 3.5v5h5"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/>'
                '<path d="M3.5 10h17M8 3v4M16 3v4M8 14h2M14 14h2M8 17h2"/>',
    "translate": '<path d="M3.5 5.5h10M8.5 3.5v2M11.5 5.5c-.8 3.6-3.4 6.6-7 8"/>'
                 '<path d="M6.5 8.5c1.3 2.2 3.2 3.8 5.5 4.8M12.5 20.5l4-9 4 9M14 17.5h5"/>',
    "inbox": '<path d="M3.5 13.5 6 5.5A2 2 0 0 1 7.9 4h8.2A2 2 0 0 1 18 5.5l2.5 8v5a2 2 0 0 1-2 2'
             'h-13a2 2 0 0 1-2-2z"/><path d="M3.5 13.5H8l1.5 2.5h5l1.5-2.5h4.5"/>',
    "mic": '<rect x="9" y="3.5" width="6" height="11" rx="3"/>'
           '<path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5"/>',
    "update": '<path d="M12 4v11M7 10.5l5 5 5-5M5 19.5h14"/>',
    "bell": '<path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 2H5z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "open": '<path d="M13.5 4.5h6v6M19.5 4.5 11 13M17.5 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8.5'
            'a2 2 0 0 1 2-2h4"/>',
    "heart": '<path d="M12 19.5s-7.5-4.4-7.5-9.7A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6'
             'c0 5.3-7.5 9.7-7.5 9.7z"/>',
    "check": '<path d="M5 12.5 9.5 17 19 7.5"/>',
    "pin": '<path d="M9 3.5h6l-1 5.5 3.5 3.5v1.5h-11V12.5L10 9z"/><path d="M12 14v6.5"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.2 1.2"/>'
            '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2"/>',
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
