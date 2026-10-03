"""Shared workbench-styled message boxes.

The uninstall entry used a bare ``QMessageBox.warning``: it inherited the settings
panel's light background, kept the platform's English "Yes"/"No" buttons and drew
its body text with the wrong colour, so it looked nothing like the rest of the
application's dialogs.  Everything that needs a confirmation or a notice box goes
through this module instead, which reuses the same background, button geometry and
accent colours as the workbench theme.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox, QWidget

from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.core.render.layers import WindowLayer
from lib.core.render.layers import get_layer_manager
from lib.script.workbench.theme import get_workbench_colors

#: 与办公页确认框同名的对象名，供 QSS 与测试定位。
CONFIRM_DIALOG_OBJECT_NAME = "WorkbenchConfirmDialog"

#: 主按钮对象名：破坏性操作（卸载、删除）用 danger 色，其余用主题粉。
DESTRUCTIVE_BUTTON_OBJECT_NAME = "WorkbenchConfirmDestructive"
PRIMARY_BUTTON_OBJECT_NAME = "WorkbenchConfirmPrimary"

#: 与语音包删除框一致的正文最小宽度，避免长句被折成很窄的一条。
DIALOG_TEXT_MIN_WIDTH = scale_px(330, min_abs=300)


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
    parent: QWidget | None,
    *,
    title: str,
    text: str,
    informative_text: str = "",
    icon: QMessageBox.Icon = QMessageBox.Information,
) -> QMessageBox:
    dialog = QMessageBox(parent)
    dialog.setObjectName(CONFIRM_DIALOG_OBJECT_NAME)
    dialog.setWindowTitle(title)
    dialog.setIcon(icon)
    dialog.setText(text)
    if informative_text:
        dialog.setInformativeText(informative_text)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    return dialog


def _apply_style(dialog: QMessageBox) -> None:
    """把 QSS 刷到对话框上，必须在标准按钮都建好之后再调用。

    ``setStandardButtons`` 之前设的样式表不会生效到之后才创建的按钮上：实测先设样式
    再建按钮时，卸载按钮停在默认的 ``surface_raised`` 底色，只有 ``:default`` 那条
    规则碰巧命中取消按钮。所以统一在建好按钮、改完文字与对象名之后再设样式表——
    ``VoicePackageManagementBar._confirm_removal`` 也是这个顺序。
    """
    dialog.setStyleSheet(confirm_dialog_stylesheet())


def _register(dialog: QMessageBox, layer_name: str) -> None:
    """Put the box on the dialog layer so it cannot end up behind the workbench."""
    get_layer_manager().register(dialog, WindowLayer.DIALOG, z=1, name=layer_name)


def _release(dialog: QMessageBox, layer_name: str) -> None:
    layer_manager = get_layer_manager()
    layer_manager.unregister(dialog)
    dialog.deleteLater()


def ask_confirmation(
    parent: QWidget | None,
    *,
    title: str,
    text: str,
    informative_text: str = "",
    confirm_text: str = "确定",
    cancel_text: str = "取消",
    destructive: bool = False,
    icon: QMessageBox.Icon = QMessageBox.Warning,
    layer_name: str = "WorkbenchConfirmation",
) -> bool:
    """Ask for confirmation with the shared dialog language.

    ``destructive`` paints the confirm button in the theme's danger colour; the
    cancel button stays the default so Enter never triggers the destructive path.
    """
    dialog = _build_dialog(
        parent,
        title=title,
        text=text,
        informative_text=informative_text,
        icon=icon,
    )
    dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
    confirm_button = dialog.button(QMessageBox.Yes)
    if confirm_button is not None:
        confirm_button.setText(confirm_text)
        confirm_button.setObjectName(
            DESTRUCTIVE_BUTTON_OBJECT_NAME if destructive else PRIMARY_BUTTON_OBJECT_NAME
        )
    cancel_button = dialog.button(QMessageBox.Cancel)
    if cancel_button is not None:
        cancel_button.setText(cancel_text)
        dialog.setEscapeButton(cancel_button)
    dialog.setDefaultButton(QMessageBox.Cancel)
    _apply_style(dialog)
    _register(dialog, layer_name)
    try:
        return dialog.exec_() == QMessageBox.Yes
    finally:
        _release(dialog, layer_name)


def show_message(
    parent: QWidget | None,
    *,
    title: str,
    text: str,
    informative_text: str = "",
    ok_text: str = "知道了",
    icon: QMessageBox.Icon = QMessageBox.Information,
    layer_name: str = "WorkbenchNotice",
) -> None:
    """Show a styled notice box; the single button is the themed primary one."""
    dialog = _build_dialog(
        parent,
        title=title,
        text=text,
        informative_text=informative_text,
        icon=icon,
    )
    dialog.setStandardButtons(QMessageBox.Ok)
    ok_button = dialog.button(QMessageBox.Ok)
    if ok_button is not None:
        ok_button.setText(ok_text)
        ok_button.setObjectName(PRIMARY_BUTTON_OBJECT_NAME)
    dialog.setDefaultButton(QMessageBox.Ok)
    _apply_style(dialog)
    _register(dialog, layer_name)
    try:
        dialog.exec_()
    finally:
        _release(dialog, layer_name)


__all__ = [
    "CONFIRM_DIALOG_OBJECT_NAME",
    "DESTRUCTIVE_BUTTON_OBJECT_NAME",
    "PRIMARY_BUTTON_OBJECT_NAME",
    "ask_confirmation",
    "confirm_dialog_stylesheet",
    "show_message",
]
