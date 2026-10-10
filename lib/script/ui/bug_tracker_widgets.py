"""Bug tracker 窗口的纯视图部件族。

本模块从 `bug_tracker_window.py` 切出 `_BugTrackerWatermarkOverlay`：水印自绘覆盖层
（读取宿主窗口上的 `_watermark_*` 文本，只画不接事件）。它是 `BugTrackerWindow`
构造时挂上去的独立子控件，不反向引用窗口类的任何符号。

行级等价：类体与前一份逐字节相同。`bug_tracker_window.py` 反向 `from ... import`
该类，挂在 `self._watermark_overlay` 的既有导入面不变。

本模块 `import PyQt5`，按 34.2 节规则登记 `frozen_ui_qt_importers`。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget

from config.scale import scale_px
from lib.script.ui.render_bridge import digit_font as get_digit_font


class _BugTrackerWatermarkOverlay(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, event) -> None:
        del event
        host = self.parent()
        if host is None:
            return

        title_text = str(getattr(host, "_watermark_title_text", "") or "").strip()
        meta_text = str(getattr(host, "_watermark_meta_text", "") or "").strip()
        hardware_text = str(getattr(host, "_watermark_hardware_text", "") or "").strip()
        corner_text = str(getattr(host, "_watermark_corner_text", "") or "").strip()
        if not any((title_text, meta_text, hardware_text, corner_text)):
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        rect = self.rect()

        title_color = QColor(110, 78, 92)
        title_color.setAlpha(78)
        detail_color = QColor(128, 92, 108)
        detail_color.setAlpha(92)

        if title_text:
            painter.setPen(title_color)
            title_font = get_digit_font(size=max(scale_px(34, min_abs=24), int(rect.height() * 0.072)))
            title_font.setBold(True)
            painter.setFont(title_font)
            title_rect = rect.adjusted(
                scale_px(22, min_abs=16),
                int(rect.height() * 0.48),
                -int(rect.width() * 0.55),
                -scale_px(26, min_abs=18),
            )
            painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignBottom, title_text)

        if hardware_text:
            painter.setPen(detail_color)
            hardware_font = get_digit_font(size=max(scale_px(11, min_abs=9), int(rect.height() * 0.015)))
            hardware_font.setBold(True)
            painter.setFont(hardware_font)
            hardware_rect = rect.adjusted(
                int(rect.width() * 0.58),
                scale_px(92, min_abs=78),
                -scale_px(24, min_abs=16),
                -int(rect.height() * 0.68),
            )
            painter.drawText(hardware_rect, Qt.AlignRight | Qt.AlignTop, hardware_text)

        if corner_text:
            painter.setPen(detail_color)
            corner_font = get_digit_font(size=max(scale_px(15, min_abs=12), int(rect.height() * 0.021)))
            corner_font.setBold(True)
            painter.setFont(corner_font)
            corner_rect = rect.adjusted(
                int(rect.width() * 0.54),
                int(rect.height() * 0.60),
                -scale_px(28, min_abs=20),
                -scale_px(34, min_abs=24),
            )
            painter.drawText(corner_rect, Qt.AlignRight | Qt.AlignBottom, corner_text)

        if meta_text:
            painter.save()
            painter.setPen(detail_color)
            meta_font = get_digit_font(size=max(scale_px(12, min_abs=9), int(rect.height() * 0.018)))
            meta_font.setBold(True)
            painter.setFont(meta_font)
            painter.translate(rect.width() - scale_px(20, min_abs=16), int(rect.height() * 0.80))
            painter.rotate(-90)
            painter.drawText(0, 0, meta_text)
            painter.restore()
