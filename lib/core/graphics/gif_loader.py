"""Decode GIF files into backend-neutral RGBA image resources."""
from __future__ import annotations

import os
from pathlib import Path

from lib.core.logger import get_logger
from .image_loader import decode_image_frames
from .resources import ImageResource, RasterFrame


logger = get_logger(__name__)


class GifLoader:
    """Load configured GIF files without creating toolkit image objects."""

    def __init__(self, gif_files: list[str]):
        self.gif_files = gif_files
        self.gifs: dict[str, ImageResource] = {}

    def load_all(self) -> dict[str, ImageResource]:
        logger.info("工作目录: %s", os.getcwd())
        logger.info("开始加载 GIF 文件...")
        for filename in self.gif_files:
            path = Path(filename)
            if path.is_file():
                frames = self._load_frames(path)
                if frames:
                    resource = ImageResource(path.stem, tuple(frames))
                    self.gifs[resource.resource_id] = resource
                    logger.info("  ✓ %s  (%d 帧)", filename, len(frames))
                else:
                    logger.warning("  ✗ %s  加载失败", filename)
            else:
                logger.warning("  - %s  文件不存在", filename)
        logger.info("共加载 %d 个动画\n", len(self.gifs))
        return self.gifs

    def _load_frames(self, filename: str | Path) -> list[RasterFrame]:
        """Read and composite a GIF into tightly packed RGBA8888 frames."""
        return list(decode_image_frames(filename))

    def get(self, name: str) -> ImageResource | None:
        return self.gifs.get(name)

    def has(self, name: str) -> bool:
        return name in self.gifs
