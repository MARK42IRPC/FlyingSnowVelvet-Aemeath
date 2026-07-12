"""桌面截图捕获工具。"""

from lib.core.logger import get_logger

logger = get_logger(__name__)


def capture_screen() -> list[bytes] | None:
    """
    捕获主屏幕截图并返回字节数组列表（Ollama 多模态格式）。

    Returns:
        包含单个图片字节数据的列表，失败时返回 None
    """
    try:
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QBuffer, QIODevice

        app = QApplication.instance()
        if app is None:
            logger.warning("[Vision] QApplication 不存在，无法截图")
            return None

        screen = app.primaryScreen()
        if screen is None:
            logger.warning("[Vision] 无法获取主屏幕")
            return None

        pixmap = screen.grabWindow(0)  # 0 表示整个屏幕

        buffer = QBuffer()
        buffer.open(QIODevice.ReadWrite)
        pixmap.save(buffer, "PNG")
        image_data = bytes(buffer.data())
        buffer.close()

        logger.debug("[Vision] 截图成功，原始大小: %d bytes", len(image_data))
        return [image_data]  # 后续在编码阶段统一压缩

    except Exception as e:
        logger.error("[Vision] 截图失败: %s", e)
        return None
