"""工作台浮窗外壳的后端中立数据：QSS 生成与主题变更判定。

浮窗（二维码登录 / 更新 / 下载 / 公告）的样式表由工作台 token 生成，主题切换事件
的载荷判定也是纯数据逻辑——两者都不需要 Qt，因此落在 `visuals/`，让 Qt 宿主与
（将来的）DX 宿主共享同一份事实。真实的 `QWidget` 外壳能力在
`lib/core/render/backends/qt/widgets/floating_window.py`。
"""

from __future__ import annotations

from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.core.render.visuals.workbench_tokens import (
    get_workbench_tokens,
    resolve_workbench_mode,
)

FLOATING_WINDOW_OBJECT_NAME = "WorkbenchFloatingWindow"


def floating_window_stylesheet(mode: str | None = None) -> str:
    """Return the shared QSS for compact floating dialogs in the given theme."""
    resolved = resolve_workbench_mode(mode)
    return _floating_window_stylesheet_for(resolved)


def _floating_window_stylesheet_for(resolved_mode: str) -> str:
    c = get_workbench_tokens(resolved_mode)
    font_family = get_ui_font_family().replace("'", "\\'")
    border = scale_px(1, min_abs=1)
    radius = scale_px(4, min_abs=3)
    control_height = scale_px(32, min_abs=28)
    return f"""
    QWidget#{FLOATING_WINDOW_OBJECT_NAME},
    QWidget#{FLOATING_WINDOW_OBJECT_NAME} * {{
        font-family: '{font_family}';
    }}
    QLabel {{
        background: transparent;
        color: {c['text']};
    }}
    QLabel#WorkbenchFloatingTitle {{
        color: {c['text']};
        font-weight: 700;
    }}
    QLabel#WorkbenchFloatingSubtitle, QLabel#WorkbenchFloatingMeta {{
        color: {c['text_muted']};
    }}
    QPushButton {{
        background: {c['surface_raised']};
        color: {c['text']};
        border: {border}px solid {c['border']};
        border-radius: {radius}px;
        min-height: {control_height}px;
        padding: 0px {scale_px(12, min_abs=10)}px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {c['surface_hover']};
        border-color: {c['cyan']};
    }}
    QPushButton:pressed {{
        background: {c['border']};
    }}
    QPushButton:disabled {{
        background: {c['surface']};
        color: {c['text_dim']};
        border-color: {c['border']};
    }}
    QPushButton#WorkbenchFloatingPrimary {{
        background: {c['pink']};
        border-color: {c['pink']};
        color: {c['canvas']};
        font-weight: 700;
    }}
    QPushButton#WorkbenchFloatingPrimary:hover {{
        background: {c['pink_hover']};
        border-color: {c['pink_hover']};
    }}
    QPushButton#WorkbenchFloatingAccent {{
        background: {c['cyan']};
        border-color: {c['cyan']};
        color: {c['canvas']};
        font-weight: 700;
    }}
    QPushButton#WorkbenchFloatingAccent:hover {{
        background: {c['pink_hover']};
        border-color: {c['pink_hover']};
    }}
    QProgressBar {{
        background: {c['surface_raised']};
        color: {c['text']};
        border: {border}px solid {c['border']};
        border-radius: {radius}px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {c['cyan']};
        border-radius: {radius}px;
    }}
    QComboBox {{
        background: {c['surface_raised']};
        color: {c['text']};
        border: {border}px solid {c['border']};
        border-radius: {radius}px;
        min-height: {control_height}px;
        padding: 0px {scale_px(30, min_abs=26)}px 0px {scale_px(9, min_abs=7)}px;
    }}
    QComboBox:hover {{
        background: {c['surface_hover']};
        border-color: {c['cyan']};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: {scale_px(26, min_abs=22)}px;
        background: transparent;
        border: none;
    }}
    QComboBox::down-arrow {{
        image: url(resc/ui/combo_down_arrow.svg);
        width: {scale_px(12, min_abs=10)}px;
        height: {scale_px(8, min_abs=6)}px;
    }}
    QComboBox QAbstractItemView {{
        background: {c['surface']};
        color: {c['text']};
        border: {border}px solid {c['border_strong']};
        outline: none;
        selection-background-color: {c['surface_hover']};
        selection-color: {c['text']};
    }}
    QTextBrowser, QTextEdit {{
        background: {c['surface']};
        color: {c['text']};
        border: {border}px solid {c['border']};
        border-radius: {radius}px;
    }}
    QScrollBar:vertical {{
        background: {c['canvas']};
        width: {scale_px(10, min_abs=8)}px;
        border: none;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border_strong']};
        min-height: {scale_px(28, min_abs=22)}px;
        border-radius: {scale_px(3, min_abs=2)}px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c['text_dim']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
        border: none;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    """ + _window_button_stylesheet(resolved_mode)


def _window_button_stylesheet(resolved_mode: str) -> str:
    """Mirror of the workbench window-button QSS using the neutral token map."""
    c = get_workbench_tokens(resolved_mode)
    radius = scale_px(4, min_abs=3)
    control_height = scale_px(32, min_abs=28)
    return f"""
    QToolButton#WorkbenchWindowButton {{
        background: transparent;
        border: none;
        border-radius: {radius}px;
        min-width: {control_height}px;
        min-height: {control_height}px;
        max-width: {control_height}px;
        max-height: {control_height}px;
        padding: 0px;
    }}
    QToolButton#WorkbenchWindowButton:hover {{
        background: {c['surface_hover']};
    }}
    QToolButton#WorkbenchWindowButton[danger="true"]:hover {{
        background: {c['danger']};
    }}
    """


def voice_installer_stylesheet(mode: str | None = None) -> str:
    """语音包安装器浮窗的分档 QSS：共享外壳 + 安装器自己的控件语言。

    比 `floating_window_stylesheet()` 多出下拉框尺寸档、两条进度条各自的 chunk 配色
    （下载走青色、解压走粉色）与三个按钮 id 的着色。颜色只从这个主题的 token 取值，
    因此切换主题时会跟着变。
    """
    resolved = resolve_workbench_mode(mode)
    c = get_workbench_tokens(resolved)
    return floating_window_stylesheet(resolved) + f"""
            QLabel {{ color: {c['text']}; background: transparent; }}
            QComboBox {{
                min-height: {scale_px(32, min_abs=28)}px;
                padding: 0px {scale_px(42, min_abs=36)}px 0px {scale_px(9, min_abs=7)}px;
                color: {c['text']};
                background: {c['surface_raised']};
                border: 1px solid {c['border']};
                border-radius: 3px;
            }}
            QComboBox:focus {{ border: 2px solid {c['cyan']}; }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: {scale_px(34, min_abs=30)}px;
                background: {c['cyan']};
                border: none;
                border-left: 1px solid {c['border']};
            }}
            QComboBox::drop-down:hover {{ background: {c['surface_hover']}; }}
            QComboBox::down-arrow {{
                image: url(resc/ui/combo_down_arrow.svg);
                width: {scale_px(12, min_abs=10)}px;
                height: {scale_px(8, min_abs=6)}px;
            }}
            QProgressBar {{
                color: {c['text']};
                background: {c['surface_raised']};
                border: 1px solid {c['border']};
                border-radius: 3px;
                text-align: center;
            }}
            QProgressBar#VoiceDownloadProgress::chunk {{ background: {c['cyan']}; }}
            QProgressBar#VoiceExtractProgress::chunk {{ background: {c['pink']}; }}
            QPushButton {{
                min-width: {scale_px(94, min_abs=82)}px;
                min-height: {scale_px(32, min_abs=28)}px;
                color: {c['text']};
                background: {c['surface_raised']};
                border: 1px solid {c['border']};
                border-radius: 3px;
                padding: 0px {scale_px(10, min_abs=8)}px;
            }}
            QPushButton:hover {{ background: {c['surface_hover']}; }}
            QPushButton#VoiceInstallerBackground {{
                color: {c['canvas']};
                background: {c['cyan']};
                font-weight: 700;
            }}
            QPushButton#VoiceInstallerBackground:hover {{
                color: {c['canvas']};
                background: {c['pink_hover']};
            }}
            QPushButton#VoiceInstallerPrimary {{
                color: {c['canvas']};
                background: {c['pink']};
                font-weight: 700;
            }}
            QPushButton#VoiceInstallerPrimary:hover {{
                color: {c['canvas']};
                background: {c['pink_hover']};
            }}
            QPushButton:disabled {{
                color: {c['text_dim']};
                background: {c['surface_raised']};
                border-color: {c['border']};
            }}
            """


def is_workbench_theme_change(event: object) -> bool:
    """Return True when a CONFIG_UPDATED payload switched the workbench theme."""
    data = getattr(event, "data", None)
    if not isinstance(data, dict):
        return False
    values = data.get("values")
    if not isinstance(values, dict):
        return False
    ui_values = values.get("UI")
    return isinstance(ui_values, dict) and "workbench_light_theme" in ui_values


__all__ = [
    "FLOATING_WINDOW_OBJECT_NAME",
    "floating_window_stylesheet",
    "is_workbench_theme_change",
    "voice_installer_stylesheet",
]
