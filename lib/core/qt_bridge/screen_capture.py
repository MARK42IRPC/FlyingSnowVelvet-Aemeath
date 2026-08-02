"""Qt implementation of the backend-neutral screen capture contract."""
from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QBuffer, QIODevice
from PyQt5.QtWidgets import QApplication

from lib.core.logger import get_logger

logger = get_logger(__name__)


class QtScreenCapture:
    """Capture the Qt primary screen and expose only encoded PNG bytes."""

    def __init__(self, application_getter: Callable[[], object | None] | None = None):
        self._application_getter = application_getter or QApplication.instance

    def capture_primary_png(self) -> bytes | None:
        try:
            app = self._application_getter()
            if app is None:
                logger.warning("[Vision] QApplication 不存在，无法截图")
                return None

            screen = app.primaryScreen()
            if screen is None:
                logger.warning("[Vision] 无法获取主屏幕")
                return None

            pixmap = screen.grabWindow(0)
            buffer = QBuffer()
            if not buffer.open(QIODevice.ReadWrite):
                logger.warning("[Vision] 无法创建截图编码缓冲区")
                return None
            try:
                if not pixmap.save(buffer, "PNG"):
                    logger.warning("[Vision] 主屏幕截图编码失败")
                    return None
                image_data = bytes(buffer.data())
            finally:
                buffer.close()

            if not image_data:
                logger.warning("[Vision] 主屏幕截图为空")
                return None
            logger.debug("[Vision] 截图成功，原始大小: %d bytes", len(image_data))
            return image_data
        except Exception as exc:
            logger.error("[Vision] 截图失败: %s", exc)
            return None


def capture_primary_screen_png() -> bytes | None:
    """Compatibility function for callers that do not own a capture backend."""
    return QtScreenCapture().capture_primary_png()
