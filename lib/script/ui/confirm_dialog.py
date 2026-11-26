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

from lib.core.render.visuals.message_box import (
    DESTRUCTIVE_BUTTON_OBJECT_NAME,
    MESSAGE_BOX_OBJECT_NAME,
    PRIMARY_BUTTON_OBJECT_NAME,
)
from lib.script.ui import render_bridge

#: 历史名称保留给既有调用方；真实对象名由 render 层共享规格维护。
CONFIRM_DIALOG_OBJECT_NAME = MESSAGE_BOX_OBJECT_NAME

#: 图标的后端中立名（``QtMessageBoxHost`` 再翻译回 QMessageBox 图标）。
ICON_INFORMATION = "information"
ICON_WARNING = "warning"
ICON_QUESTION = "question"
ICON_CRITICAL = "critical"


def confirm_dialog_stylesheet(mode: str | None = None) -> str:
    """Return the QSS shared by every workbench confirm/notice box."""
    return render_bridge.message_box_stylesheet(mode)


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
    host.apply_stylesheet(render_bridge.message_box_stylesheet)
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
