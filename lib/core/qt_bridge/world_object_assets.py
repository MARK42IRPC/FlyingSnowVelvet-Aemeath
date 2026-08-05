"""Qt conversions for backend-neutral world-object image resources."""
from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap, QTransform

from lib.core.graphics.resources import ImageResource
from lib.core.qt_bridge.gif_loader import qimage_from_raster_frame


@dataclass(frozen=True)
class PixmapPair:
    """A normal and horizontally mirrored pixmap made at the Qt boundary."""

    pixmap: QPixmap
    flipped_pixmap: QPixmap
    size: tuple[int, int]


def _qimage_frames(resource: ImageResource) -> list[QImage]:
    return [qimage_from_raster_frame(frame) for frame in resource.frames]


def pixmap_pair_from_resource(resource: ImageResource) -> PixmapPair:
    """Convert one static resource to normal and mirrored Qt pixmaps."""
    pixmap = QPixmap.fromImage(qimage_from_raster_frame(resource.frames[0]))
    flipped = pixmap.transformed(
        QTransform().scale(-1, 1),
        Qt.SmoothTransformation,
    )
    return PixmapPair(pixmap, flipped, resource.size)


def image_frame_pair_from_resource(
    resource: ImageResource,
) -> tuple[list[QImage], list[QImage]]:
    """Convert animation frames and their mirrored counterparts for Qt."""
    frames = _qimage_frames(resource)
    return frames, [frame.mirrored(horizontal=True, vertical=False) for frame in frames]
