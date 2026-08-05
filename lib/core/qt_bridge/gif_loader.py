"""Qt image transformations used by toolkit-owned world objects."""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage

from lib.core.graphics.resources import RasterFrame


def qimage_from_raster_frame(frame: RasterFrame) -> QImage:
    """Copy a core RGBA frame into Qt-owned image memory."""
    return QImage(
        frame.pixels,
        frame.width,
        frame.height,
        frame.stride,
        QImage.Format_RGBA8888,
    ).copy()


def scale_frame(frame: QImage, size: tuple[int, int]) -> QImage:
    """Scale a frame with nearest-neighbor sampling."""
    return frame.scaled(
        size[0],
        size[1],
        Qt.IgnoreAspectRatio,
        Qt.FastTransformation,
    )


def flip_frame(frame: QImage) -> QImage:
    """Mirror a frame horizontally."""
    return frame.mirrored(horizontal=True, vertical=False)
