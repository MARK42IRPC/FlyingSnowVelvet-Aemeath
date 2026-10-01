"""Qt 字体与文本度量的后端实现，供后端中立协议取用。

字体族注册与 `QFont` 构造属于平台能力（档位 B），因此留在 `runtime/`；
文本度量是绘制期渲染事实（档位 A），实现放在 `drawing/`。本模块只做交接。
"""
from __future__ import annotations

from lib.core.render.backends.qt.drawing.text_metrics import QtTextMetrics
from lib.core.render.backends.qt.runtime import font as qt_font


class QtFontProvider:
    """Expose the Qt font registry through the neutral provider protocol."""

    def ui_font(self, size: int | None = None):
        return qt_font.get_ui_font(size)

    def digit_font(self, size: int | None = None):
        return qt_font.get_digit_font(size)

    def cmd_font(self, size: int | None = None):
        return qt_font.get_cmd_font(size)

    def ui_font_family(self) -> str:
        return qt_font.get_ui_font_family()

    def apply_ui_font_tree(self, widget) -> None:
        qt_font.apply_ui_font_tree(widget)


def create_qt_font_provider() -> QtFontProvider:
    return QtFontProvider()


def create_qt_text_metrics(default_font, digit_font=None, *, side_font=None) -> QtTextMetrics:
    return QtTextMetrics(default_font, digit_font, side_font=side_font)


__all__ = [
    "QtFontProvider",
    "create_qt_font_provider",
    "create_qt_text_metrics",
]
