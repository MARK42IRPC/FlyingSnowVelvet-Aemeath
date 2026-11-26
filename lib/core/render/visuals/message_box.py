"""共享工作台确认/提示框的后端中立视觉规格。

按钮语义和模态执行由 ``message_box_host.py`` 承接；本模块只生成依赖工作台 token
的 QSS 文本，避免游戏管理器、办公页和语音包控件各自复制一套弹窗颜色与尺寸。
"""

from __future__ import annotations

from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.core.render.visuals.workbench_tokens import get_workbench_tokens


MESSAGE_BOX_OBJECT_NAME = "WorkbenchConfirmDialog"
DESTRUCTIVE_BUTTON_OBJECT_NAME = "WorkbenchConfirmDestructive"
PRIMARY_BUTTON_OBJECT_NAME = "WorkbenchConfirmPrimary"
DIALOG_TEXT_MIN_WIDTH = scale_px(330, min_abs=300)


def message_box_stylesheet(mode: str | None = None) -> str:
    """返回共享确认/提示框 QSS；颜色随工作台明暗主题变化。"""
    colors = get_workbench_tokens(mode)
    font_family = get_ui_font_family().replace("'", "\\'")
    border = scale_px(1, min_abs=1)
    radius = scale_px(4, min_abs=3)
    button_height = scale_px(31, min_abs=27)
    button_width = scale_px(92, min_abs=80)
    horizontal_padding = scale_px(14, min_abs=11)
    return f"""
        QMessageBox#{MESSAGE_BOX_OBJECT_NAME} {{
            background: {colors['canvas']};
            color: {colors['text']};
            font-family: '{font_family}';
        }}
        QMessageBox#{MESSAGE_BOX_OBJECT_NAME} QLabel {{
            min-width: {DIALOG_TEXT_MIN_WIDTH}px;
            color: {colors['text']};
            background: transparent;
        }}
        QMessageBox#{MESSAGE_BOX_OBJECT_NAME} QPushButton {{
            min-width: {button_width}px;
            min-height: {button_height}px;
            padding: 0px {horizontal_padding}px;
            color: {colors['text']};
            background: {colors['surface_raised']};
            border: {border}px solid {colors['border']};
            border-radius: {radius}px;
            font-weight: 600;
        }}
        QMessageBox#{MESSAGE_BOX_OBJECT_NAME} QPushButton:hover {{
            background: {colors['surface_hover']};
            border-color: {colors['cyan']};
        }}
        QMessageBox#{MESSAGE_BOX_OBJECT_NAME} QPushButton:default,
        QMessageBox#{MESSAGE_BOX_OBJECT_NAME} QPushButton#{PRIMARY_BUTTON_OBJECT_NAME} {{
            color: {colors['canvas']};
            background: {colors['pink']};
            border-color: {colors['pink']};
        }}
        QMessageBox#{MESSAGE_BOX_OBJECT_NAME} QPushButton:default:hover,
        QMessageBox#{MESSAGE_BOX_OBJECT_NAME} QPushButton#{PRIMARY_BUTTON_OBJECT_NAME}:hover {{
            background: {colors['pink_hover']};
            border-color: {colors['pink_hover']};
        }}
        QMessageBox#{MESSAGE_BOX_OBJECT_NAME} QPushButton#{DESTRUCTIVE_BUTTON_OBJECT_NAME} {{
            color: {colors['canvas']};
            background: {colors['danger']};
            border-color: {colors['danger']};
        }}
        QMessageBox#{MESSAGE_BOX_OBJECT_NAME} QPushButton#{DESTRUCTIVE_BUTTON_OBJECT_NAME}:hover {{
            color: {colors['canvas']};
            background: {colors['danger']};
            border-color: {colors['danger']};
        }}
    """


__all__ = [
    "DIALOG_TEXT_MIN_WIDTH",
    "DESTRUCTIVE_BUTTON_OBJECT_NAME",
    "MESSAGE_BOX_OBJECT_NAME",
    "PRIMARY_BUTTON_OBJECT_NAME",
    "message_box_stylesheet",
]
