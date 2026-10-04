"""Shared workbench-styled message boxes.

The uninstall entry used a bare ``QMessageBox.warning``: it inherited the settings
panel's light background, kept the platform's English "Yes"/"No" buttons and drew
its body text with the wrong colour, so it looked nothing like the rest of the
application's dialogs.  Everything that needs a confirmation or a notice box goes
through this module instead, which reuses the same background, button geometry and
accent colours as the workbench theme.

本模块不再 ``import PyQt5``：它只声明"问什么、有哪些按钮、哪一色"，真实
``QMessageBox`` 与模态循环由 ``lib/script/ui/render_bridge.py`` 的
``create_message_box_host()`` 提供（Qt 下是 ``QtMessageBoxHost``）。
"""

from __future__ import annotations

from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.script.ui import render_bridge
from lib.script.workbench.theme import get_workbench_colors

#: 与办公页确认框同名的对象名，供 QSS 与测试定位。
CONFIRM_DIALOG_OBJECT_NAME = "WorkbenchConfirmDialog"

#: 主按钮对象名：破坏性操作（卸载、删除）用 danger 色，其余用主题粉。
DESTRUCTIVE_BUTTON_OBJECT_NAME = "WorkbenchConfirmDestructive"
PRIMARY_BUTTON_OBJECT_NAME = "WorkbenchConfirmPrimary"

#: 与语音包删除框一致的正文最小宽度，避免长句被折成很窄的一条。
DIALOG_TEXT_MIN_WIDTH = scale_px(330, min_abs=300)

#: 图标的后端中立名（``QtMessageBoxHost`` 再翻译回 QMessageBox 图标）。
ICON_INFORMATION = "information"
ICON_WARNING = "warning"
ICON_QUESTION = "question"
ICON_CRITICAL = "critical"


def confirm_dialog_stylesheet(mode: str | None = None) -> str:
    """Return the QSS shared by every workbench confirm/notice box."""
    c = get_workbench_colors(mode)
    font_family = get_ui_font_family().replace("'", "\\'")
    border = scale_px(1, min_abs=1)
    radius = scale_px(4, min_abs=3)
    button_height = scale_px(31, min_abs=27)
    button_width = scale_px(92, min_abs=80)
    horizontal_padding = scale_px(14, min_abs=11)
    return f"""
        QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME} {{
            background: {c.canvas};
            color: {c.text};
            font-family: '{font_family}';
        }}
        QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME} QLabel {{
            min-width: {DIALOG_TEXT_MIN_WIDTH}px;
            color: {c.text};
            background: transparent;
        }}
        QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME} QPushButton {{
            min-width: {button_width}px;
            min-height: {button_height}px;
            padding: 0px {horizontal_padding}px;
            color: {c.text};
            background: {c.surface_raised};
            border: {border}px solid {c.border};
            border-radius: {radius}px;
            font-weight: 600;
        }}
        QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME} QPushButton:hover {{
            background: {c.surface_hover};
            border-color: {c.cyan};
        }}
        QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME} QPushButton:default,
        QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME} QPushButton#{PRIMARY_BUTTON_OBJECT_NAME} {{
            color: {c.canvas};
            background: {c.pink};
            border-color: {c.pink};
        }}
        QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME} QPushButton:default:hover,
        QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME} QPushButton#{PRIMARY_BUTTON_OBJECT_NAME}:hover {{
            background: {c.pink_hover};
            border-color: {c.pink_hover};
        }}
        QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME} QPushButton#{DESTRUCTIVE_BUTTON_OBJECT_NAME} {{
            color: {c.canvas};
            background: {c.danger};
            border-color: {c.danger};
        }}
        QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME} QPushButton#{DESTRUCTIVE_BUTTON_OBJECT_NAME}:hover {{
            background: {c.pink_hover};
            border-color: {c.pink_hover};
        }}
    """


def _build_dialog(
    parent,
    *,
    title: str,
    text: str,
    informative_text: str = "",
    icon: str = ICON_INFORMATION,
):
    """按共享外观契约构造一个模态消息框宿主（不含按钮组合）。"""
    host = render_bridge.create_message_box_host(
        parent, object_name=CONFIRM_DIALOG_OBJECT_NAME
    )
    host.set_title(title)
    host.set_icon(icon)
    host.set_text(text)
    host.set_informative_text(informative_text)
    return host


def ask_confirmation(
    parent,
    *,
    title: str,
    text: str,
    informative_text: str = "",
    confirm_text: str = "确定",
    cancel_text: str = "取消",
    destructive: bool = False,
    icon: str = ICON_WARNING,
    layer_name: str = "WorkbenchConfirmation",
) -> bool:
    """Ask for confirmation with the shared dialog language.

    ``destructive`` paints the confirm button in the theme's danger colour; the
    cancel button stays the default so Enter never triggers the destructive path.
    """
    host = _build_dialog(
        parent,
        title=title,
        text=text,
        informative_text=informative_text,
        icon=icon,
    )
    host.set_standard_buttons(("yes", "cancel"))
    host.set_button_text("yes", confirm_text)
    host.set_button_object_name(
        "yes",
        DESTRUCTIVE_BUTTON_OBJECT_NAME if destructive else PRIMARY_BUTTON_OBJECT_NAME,
    )
    host.set_button_text("cancel", cancel_text)
    host.set_escape_button("cancel")
    # 默认键落在取消：Enter 永远不触发破坏性路径。
    host.set_default_button("cancel")
    host.apply_stylesheet(confirm_dialog_stylesheet)
    return host.exec_modal(layer_name=layer_name) == "yes"


def show_message(
    parent,
    *,
    title: str,
    text: str,
    informative_text: str = "",
    ok_text: str = "知道了",
    icon: str = ICON_INFORMATION,
    layer_name: str = "WorkbenchNotice",
) -> None:
    """Show a styled notice box; the single button is the themed primary one."""
    host = _build_dialog(
        parent,
        title=title,
        text=text,
        informative_text=informative_text,
        icon=icon,
    )
    host.set_standard_buttons(("ok",))
    host.set_button_text("ok", ok_text)
    host.set_button_object_name("ok", PRIMARY_BUTTON_OBJECT_NAME)
    host.set_default_button("ok")
    host.apply_stylesheet(confirm_dialog_stylesheet)
    host.exec_modal(layer_name=layer_name)


__all__ = [
    "CONFIRM_DIALOG_OBJECT_NAME",
    "DESTRUCTIVE_BUTTON_OBJECT_NAME",
    "ICON_CRITICAL",
    "ICON_INFORMATION",
    "ICON_QUESTION",
    "ICON_WARNING",
    "PRIMARY_BUTTON_OBJECT_NAME",
    "ask_confirmation",
    "confirm_dialog_stylesheet",
    "show_message",
]
