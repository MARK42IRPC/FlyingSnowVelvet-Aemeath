"""雪绒论坛共用取色控件（产品垫片）。

雪绒论坛共用取色控件的实现已按《render 层边界契约》第 3 节的档位 D 下沉到
`lib/core/render/backends/qt/widgets/forum_color_control.py`。这里按原名再导出（经 `render_bridge.tier_d_reexports()` 解析，
垫片本身不得静态 import 档位 D），既有导入面不变。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    Qt,
    pyqtSignal,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
    scale_px,
    MAX_LIGHTNESS,
    MIN_LIGHTNESS,
    ForumColorSlider,
    color_to_hsl,
    hsl_color,
    hue_gradient_stops,
    forum_card_text_color,
    configure_font_factory,
    format_button_font,
    ForumColorToggle,
    ForumColorControl,
) = render_bridge.tier_d_reexports("forum_color_control", (
    "Qt",
    "pyqtSignal",
    "QColor",
    "QPainter",
    "QPainterPath",
    "QPen",
    "QCheckBox",
    "QFrame",
    "QHBoxLayout",
    "QLabel",
    "QStyle",
    "QStyleOptionButton",
    "QVBoxLayout",
    "QWidget",
    "scale_px",
    "MAX_LIGHTNESS",
    "MIN_LIGHTNESS",
    "ForumColorSlider",
    "color_to_hsl",
    "hsl_color",
    "hue_gradient_stops",
    "forum_card_text_color",
    "configure_font_factory",
    "format_button_font",
    "ForumColorToggle",
    "ForumColorControl",
))

render_bridge.configure_forum_color_control_font(render_bridge.ui_font)

render_bridge.configure_forum_color_picker_backend(render_bridge.create_draw_backend)

__all__ = [
    "Qt",
    "pyqtSignal",
    "QColor",
    "QPainter",
    "QPainterPath",
    "QPen",
    "QCheckBox",
    "QFrame",
    "QHBoxLayout",
    "QLabel",
    "QStyle",
    "QStyleOptionButton",
    "QVBoxLayout",
    "QWidget",
    "scale_px",
    "MAX_LIGHTNESS",
    "MIN_LIGHTNESS",
    "ForumColorSlider",
    "color_to_hsl",
    "hsl_color",
    "hue_gradient_stops",
    "forum_card_text_color",
    "configure_font_factory",
    "format_button_font",
    "ForumColorToggle",
    "ForumColorControl",
]
