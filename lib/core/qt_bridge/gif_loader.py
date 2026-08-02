"""Qt GIF loading and frame transformation backend."""
from __future__ import annotations

import os

from PIL import Image, ImageSequence
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage

from lib.core.logger import get_logger

logger = get_logger(__name__)


class GifLoader:
    """Load GIF frames as QImage objects for the Qt desktop backend."""

    def __init__(self, gif_files: list[str]):
        self.gif_files = gif_files
        self.gifs: dict[str, list[QImage]] = {}

    def load_all(self) -> dict[str, list[QImage]]:
        logger.info("工作目录: %s", os.getcwd())
        logger.info("开始加载 GIF 文件...")
        for filename in self.gif_files:
            if os.path.exists(filename):
                frames = self._load_frames(filename)
                if frames:
                    key = os.path.basename(filename).replace(".gif", "")
                    self.gifs[key] = frames
                    logger.info("  ✓ %s  (%d 帧)", filename, len(frames))
                else:
                    logger.warning("  ✗ %s  加载失败", filename)
            else:
                logger.warning("  - %s  文件不存在", filename)
        logger.info("共加载 %d 个动画\n", len(self.gifs))
        return self.gifs

    def _load_frames(self, filename: str) -> list[QImage]:
        """Read and composite GIF frames before converting them to QImage."""
        frames = []
        try:
            image = Image.open(filename)
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
                frames.append(
                    QImage(
                        data,
                        width,
                        height,
                        QImage.Format_RGBA8888,
                    ).copy()
                )
        except Exception as exc:
            logger.error("    加载 %s 出错: %s", filename, exc)
        return frames

    def get(self, name: str) -> list[QImage]:
        return self.gifs.get(name, [])

    def has(self, name: str) -> bool:
        return name in self.gifs


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
