"""QML image provider for everything TDLib downloads, for every logged-in account.

    image://tg/<account>/avatar/<file_id>            circle
    image://tg/<account>/media/<file_id>/<radius>    cover-cropped, rounded corners
    image://tg/<account>/mini/<file_id>/<radius>     same, from TDLib's inline minithumbnail
    image://tg/<account>/sticker/<file_id>           fit, transparency kept; animated
                                                     stickers (TGS/WebM) show their first frame
    image://tg/<account>/full/<file_id>              the file as is (photo viewer)

<account> is FileManager.account: each TDLib instance numbers its files on its own, and QML
caches images by URL. Radius is in physical pixels (QML multiplies by devicePixelRatio).
Set `sourceSize` in QML: it becomes the requested size here.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter, QPainterPath
from PySide6.QtQuick import QQuickImageProvider

from ..store.files import FileManager
from .animation import first_frame, sniff

_DEFAULT_SIDE = 96
_STICKER_SIDE = 256


class TdImageProvider(QQuickImageProvider):
    def __init__(self, files: FileManager | None = None) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._accounts: dict[str, FileManager] = {}
        if files is not None:
            self.add(files)

    def add(self, files: FileManager) -> None:
        self._accounts[files.account] = files

    def remove(self, account: str, files: FileManager | None = None) -> None:
        """Forget an account; with `files`, only if that FileManager is still the one used
        (a re-created session of the same account may have replaced it)."""
        if files is None or self._accounts.get(account) is files:
            self._accounts.pop(account, None)

    def requestImage(self, image_id: str, size: QSize, requested_size: QSize) -> QImage:
        parts = image_id.split("/")
        if len(parts) < 3 or parts[0] not in self._accounts:
            return QImage()
        files = self._accounts[parts[0]]
        kind = parts[1]
        try:
            file_id = int(parts[2])
            radius = int(parts[3]) if len(parts) > 3 else 0
        except ValueError:
            return QImage()

        width = max(0, requested_size.width())
        height = max(0, requested_size.height())
        if kind == "mini":
            data = files.minithumbnails.get(file_id)
            source = QImage.fromData(data) if data else QImage()
        else:
            path = files.path(file_id)
            if not path:
                return QImage()
            if kind == "sticker" and sniff(path) in ("tgs", "webm"):
                source = first_frame(path, width or _STICKER_SIDE, height or _STICKER_SIDE)
            else:
                reader = QImageReader(path)
                reader.setAutoTransform(True)  # EXIF orientation of full-size photos
                source = reader.read()
        if source.isNull():
            return QImage()

        match kind:
            case "avatar":
                side = max(width, height) or _DEFAULT_SIDE
                return rounded(source, side, side, side / 2)
            case "media" | "mini":
                if not width or not height:
                    width, height = source.width(), source.height()
                return rounded(source, width, height, radius)
            case "sticker" | "full":
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
