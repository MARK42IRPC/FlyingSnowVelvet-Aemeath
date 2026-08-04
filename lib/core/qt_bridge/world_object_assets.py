"""Qt asset helpers for desktop world-object managers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageSequence
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap, QTransform


@dataclass(frozen=True)
class PixmapPair:
    """A normal and horizontally mirrored pixmap with its rendered size."""

    pixmap: QPixmap
    flipped_pixmap: QPixmap
    size: tuple[int, int]


def _existing_path(path: str | Path) -> Path | None:
    resolved = Path(path)
    return resolved if resolved.is_file() else None


def load_pixmap(path: str | Path) -> QPixmap | None:
    """Load a pixmap, returning None when the resource is missing or invalid."""
    resolved = _existing_path(path)
    if resolved is None:
        return None
    pixmap = QPixmap(str(resolved))
    return None if pixmap.isNull() else pixmap


def flip_pixmap(pixmap: QPixmap) -> QPixmap:
    """Mirror a pixmap horizontally."""
    return pixmap.transformed(
        QTransform().scale(-1, 1),
        Qt.SmoothTransformation,
    )


def load_stretched_pixmap_pair(
    path: str | Path,
    size: tuple[int, int],
) -> PixmapPair | None:
    """Load a pixmap pair scaled exactly to size."""
    pixmap = load_pixmap(path)
    if pixmap is None:
        return None
    width, height = int(size[0]), int(size[1])
    scaled = pixmap.scaled(
        width,
        height,
        Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation,
    )
    return PixmapPair(scaled, flip_pixmap(scaled), (scaled.width(), scaled.height()))


def load_height_scaled_pixmap_pair(path: str | Path, height: int) -> PixmapPair | None:
    """Load a pixmap pair scaled to a target height while preserving aspect ratio."""
    pixmap = load_pixmap(path)
    if pixmap is None:
        return None
    scaled = pixmap.scaledToHeight(int(height), Qt.SmoothTransformation)
    return PixmapPair(scaled, flip_pixmap(scaled), (scaled.width(), scaled.height()))


def load_width_scaled_pixmap_pair(path: str | Path, width: int) -> PixmapPair | None:
    """Load a pixmap pair scaled to a target width while preserving aspect ratio."""
    pixmap = load_pixmap(path)
    if pixmap is None:
        return None
    scaled = pixmap.scaledToWidth(int(width), Qt.SmoothTransformation)
    return PixmapPair(scaled, flip_pixmap(scaled), (scaled.width(), scaled.height()))


def scale_pixmap_keep_aspect(pixmap: QPixmap, size: tuple[int, int]) -> QPixmap:
    """Scale a pixmap to fit inside size while preserving aspect ratio."""
    return pixmap.scaled(
        int(size[0]),
        int(size[1]),
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )


def scale_pixmap(pixmap: QPixmap, size: tuple[int, int]) -> QPixmap:
    """Scale a pixmap to size without preserving aspect ratio."""
    return pixmap.scaled(
        int(size[0]),
        int(size[1]),
        Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation,
    )


def load_composited_gif_frames(path: str | Path) -> list[QImage]:
    """Load GIF frames as composited Qt images."""
    resolved = _existing_path(path)
    if resolved is None:
        return []

    frames: list[QImage] = []
    with Image.open(resolved) as image:
        size = image.size
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        for frame in ImageSequence.Iterator(image):
            disposal = frame.info.get("disposal", 2)
            offset = frame.info.get("offset", (0, 0))
            frame_rgba = frame.convert("RGBA")
            if disposal == 2:
                canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            canvas.paste(frame_rgba, offset, frame_rgba)
            width, height = canvas.size
            data = canvas.tobytes("raw", "RGBA")
            frames.append(QImage(data, width, height, QImage.Format_RGBA8888).copy())
    return frames


def flip_image(frame: QImage) -> QImage:
    """Mirror an image horizontally."""
    return frame.mirrored(horizontal=True, vertical=False)


def load_gif_frame_pair(path: str | Path) -> tuple[list[QImage], list[QImage]]:
    """Load normal and horizontally mirrored GIF frames."""
    frames = load_composited_gif_frames(path)
    return frames, [flip_image(frame) for frame in frames]
