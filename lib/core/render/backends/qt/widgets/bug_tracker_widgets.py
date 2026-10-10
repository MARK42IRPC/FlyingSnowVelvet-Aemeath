"""故障跟踪窗口的水印自绘覆盖层（档位 D 宿主）。

`lib/script/ui/bug_tracker_widgets.py` 的 `_BugTrackerWatermarkOverlay` 是纯 Qt 控件：它是
`QWidget` 子类，只做「读宿主窗口身上的 `_watermark_*` 文本 + 按 `QPainter` 画上去」这类
控件工具包事实，不看窗口状态、也不反向引用窗口类。因此从产品侧下沉到 toolkit 宿主。

按 `doc/render层边界契约.md` 档位 D 的判定标准：持有 `QWidget.rect()` / `QPainter`、且绘制
实现不由本模块构造的就是档位 D。这里不构造绘制实现、不 import 档位 A（`drawing/`），
也不 import `lib.script`——取字体的入口与 `forum_images` 同形：默认走档位 B 的字体提供者，
产品面可用 `configure_font_factory()` 显式注入同一个提供者。

产品侧 `lib/script/ui/bug_tracker_widgets.py` 按原名再导出，冻结清单里不再有它；
`bug_tracker_window.py` 的导入面与调用点零改动。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget

from config.scale import scale_px

#: 字体取用入口，类型是 `(size: int | None) -> QFont`；`configure_font_factory()` 可覆盖它。
#: 默认值是档位 B 的字体服务（`runtime/providers.py` 的 `QtFontProvider`），构造第一个控件时
#: 才去注册表取；`lib/script/ui/render_bridge.py` 的 `digit_font()` 取的是同一个提供者，
#: 所以产品面注不注入都得到同一个 `QFont`——注入只是让产品面显式装配（与 `forum_images` 同形）。
_font_factory = None


def _default_font_factory():
    """档位 B 的字体提供者；未注册时退回 Qt 运行时字体注册表（也属档位 B）。"""
    from lib.core.render.registry import get_font_provider

    provider = get_font_provider()
    if provider is not None:
        return provider.digit_font
    from lib.core.render.backends.qt.runtime.font import get_digit_font

    return get_digit_font


def configure_font_factory(factory) -> None:
    """安装水印字体取用入口（档位 D 的装配点，`lib/script/ui/bug_tracker_widgets.py` 用它注入）。"""
    global _font_factory
    _font_factory = factory


def _digit_font(size: int | None = None):
    factory = _font_factory or _default_font_factory()
    return factory(size)


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
            title_font = _digit_font(size=max(scale_px(34, min_abs=24), int(rect.height() * 0.072)))
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
            hardware_font = _digit_font(size=max(scale_px(11, min_abs=9), int(rect.height() * 0.015)))
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
            corner_font = _digit_font(size=max(scale_px(15, min_abs=12), int(rect.height() * 0.021)))
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
            meta_font = _digit_font(size=max(scale_px(12, min_abs=9), int(rect.height() * 0.018)))
            meta_font.setBold(True)
            painter.setFont(meta_font)
            painter.translate(rect.width() - scale_px(20, min_abs=16), int(rect.height() * 0.80))
            painter.rotate(-90)
            painter.drawText(0, 0, meta_text)
            painter.restore()


__all__ = [
    "_BugTrackerWatermarkOverlay",
]
