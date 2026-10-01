"""QML image provider for everything TDLib downloads.

    image://tg/avatar/<file_id>            circle
    image://tg/media/<file_id>/<radius>    cover-cropped to the requested size, rounded corners
    image://tg/mini/<file_id>/<radius>     same, from TDLib's inline minithumbnail (placeholder)
    image://tg/sticker/<file_id>           fit into the requested size, transparency kept

Radius is in physical pixels (QML multiplies by devicePixelRatio). Set `sourceSize` in QML:
it becomes the requested size here.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath
from PySide6.QtQuick import QQuickImageProvider

from ..store.files import FileManager

_DEFAULT_SIDE = 96


class TdImageProvider(QQuickImageProvider):
    def __init__(self, files: FileManager) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._files = files

    def requestImage(self, image_id: str, size: QSize, requested_size: QSize) -> QImage:
        kind, _, rest = image_id.partition("/")
        parts = rest.split("/")
        try:
            file_id = int(parts[0])
            radius = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            return QImage()

        if kind == "mini":
            data = self._files.minithumbnails.get(file_id)
            source = QImage.fromData(data) if data else QImage()
        else:
            path = self._files.path(file_id)
            source = QImage(path) if path else QImage()
        if source.isNull():
            return QImage()

        width = max(0, requested_size.width())
        height = max(0, requested_size.height())
        match kind:
            case "avatar":
                side = max(width, height) or _DEFAULT_SIDE
                return rounded(source, side, side, side / 2)
            case "media" | "mini":
                if not width or not height:
                    width, height = source.width(), source.height()
                return rounded(source, width, height, radius)
            case "sticker":
                if not width or not height:
                    return source
                return source.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        return QImage()


def rounded(source: QImage, width: int, height: int, radius: float) -> QImage:
    """Cover-crop `source` to width x height and clip to a rounded rectangle."""
    result = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    scaled = source.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
    x = (scaled.width() - width) / 2
    y = (scaled.height() - height) / 2

    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if radius > 0:
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
        painter.setClipPath(path)
    painter.drawImage(QRectF(0, 0, width, height), scaled, QRectF(x, y, width, height))
    painter.end()
    return result
