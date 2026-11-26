"""Qt 模态消息框宿主：把后端中立的确认/提示描述渲染成真实 ``QMessageBox``。

这是控件层"描述 + 后端渲染"结构里的后端那一半，与 ``control_host.py`` 同类（档位 D）。
它只做 Qt 事实：``QMessageBox`` 的构造、图标/按钮/默认键/逃逸键、样式表应用、层级注册
与 ``exec_()`` 模态循环。确认/提示的公共按钮语义和工作台 QSS 也集中在本模块的两个
helper 中，业务控件不得再复制原生消息框。

因此产品侧 ``lib.script.ui`` 不需要导入 PyQt；它只经 ``render_bridge`` 声明"问什么、
有哪些按钮、用哪种配色"，真实窗口由本模块持有。换后端时替换的是这一层，不是确认框语义。
"""
from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox, QWidget

from lib.core.render.layers import WindowLayer
from lib.core.render.layers import get_layer_manager
from lib.core.render.visuals.message_box import (
    DESTRUCTIVE_BUTTON_OBJECT_NAME,
    MESSAGE_BOX_OBJECT_NAME,
    PRIMARY_BUTTON_OBJECT_NAME,
    message_box_stylesheet,
)


#: 与 ``QMessageBox`` 图标一一对应的后端中立名。
ICON_INFORMATION = "information"
ICON_WARNING = "warning"
ICON_QUESTION = "question"
ICON_CRITICAL = "critical"

_ICON_MAP = {
    ICON_INFORMATION: QMessageBox.Information,
    ICON_WARNING: QMessageBox.Warning,
    ICON_QUESTION: QMessageBox.Question,
    ICON_CRITICAL: QMessageBox.Critical,
}


class QtMessageBoxHost:
    """一个真实 ``QMessageBox`` 的持有者与模态执行器。

    调用方先按需 ``set_*`` 描述按钮与文字，再 ``apply_stylesheet`` 刷 QSS，
    最后 ``exec_modal`` 阻塞运行并把结果翻译回后端中立名。
    """

    def __init__(self, parent: QWidget | None = None, *, object_name: str = "") -> None:
        self._box = QMessageBox(parent)
        if object_name:
            self._box.setObjectName(object_name)
        self._box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._layer_name: str | None = None

    # ── 描述字段 ──────────────────────────────────────────────────
    def set_title(self, title: str) -> None:
        self._box.setWindowTitle(str(title))

    def set_text(self, text: str) -> None:
        self._box.setText(str(text))

    def set_informative_text(self, text: str) -> None:
        if text:
            self._box.setInformativeText(str(text))

    def set_icon(self, icon: str) -> None:
        self._box.setIcon(_ICON_MAP.get(str(icon), QMessageBox.Information))

    def set_standard_buttons(self, names) -> None:
        """按后端中立名设置标准按钮组合（``ok`` / ``yes`` / ``no`` / ``cancel``）。"""
        flags = QMessageBox.NoButton
        for name in names:
            flags |= _STANDARD_BUTTONS.get(str(name), QMessageBox.NoButton)
        self._box.setStandardButtons(flags)

    def set_button_text(self, name: str, text: str) -> None:
        button = self._box.button(_STANDARD_BUTTONS[str(name)])
        if button is not None:
            button.setText(str(text))

    def set_button_object_name(self, name: str, object_name: str) -> None:
        button = self._box.button(_STANDARD_BUTTONS[str(name)])
        if button is not None:
            button.setObjectName(str(object_name))

    def set_default_button(self, name: str) -> None:
        self._box.setDefaultButton(_STANDARD_BUTTONS[str(name)])

    def set_escape_button(self, name: str) -> None:
        button = self._box.button(_STANDARD_BUTTONS[str(name)])
        if button is not None:
            self._box.setEscapeButton(button)

    def apply_stylesheet(self, stylesheet_provider: Callable[[], str]) -> None:
        """在建好全部按钮之后刷样式表。

        ``setStandardButtons`` 之前设的 QSS 不会作用到之后才创建的按钮上，所以样式表
        必须最后由调用方给出、在此刻应用（调用方与本模块共同保证顺序）。
        """
        self._box.setStyleSheet(stylesheet_provider())

    # ── 模态执行与层级 ────────────────────────────────────────────
    def exec_modal(self, *, layer_name: str | None = None) -> str:
        """注册到对话框层、阻塞运行，并把结果翻译成后端中立名。

        返回值：``yes`` / ``ok`` / ``cancel`` / ``escape`` 之一，关闭且未选择时为 ``""``。
        """
        if layer_name:
            self._layer_name = layer_name
            get_layer_manager().register(
                self._box, WindowLayer.DIALOG, z=1, name=layer_name
            )
        try:
            return _RESULT_NAMES.get(int(self._box.exec_()), "")
        finally:
            self._release()

    def _release(self) -> None:
        layer_manager = get_layer_manager()
        try:
            layer_manager.unregister(self._box)
        except Exception:
            pass
        self._box.deleteLater()

    # ── 测试/诊断用视图 ───────────────────────────────────────────
    def widget(self) -> QMessageBox:
        """返回底层 ``QMessageBox``，仅供测试与诊断读取。"""
        return self._box


def _build_dialog(
    parent,
    *,
    title: str,
    text: str,
    informative_text: str = "",
    icon: str = ICON_INFORMATION,
) -> QtMessageBoxHost:
    host = QtMessageBoxHost(parent, object_name=MESSAGE_BOX_OBJECT_NAME)
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
    """显示统一工作台确认框；Enter/Esc 都落在取消，避免误触危险操作。"""
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
    host.set_default_button("cancel")
    host.apply_stylesheet(message_box_stylesheet)
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
    """显示统一工作台提示框；单按钮同时显式绑定默认键与 Esc。"""
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
    host.set_escape_button("ok")
    host.apply_stylesheet(message_box_stylesheet)
    host.exec_modal(layer_name=layer_name)


_STANDARD_BUTTONS = {
    "ok": QMessageBox.Ok,
    "yes": QMessageBox.Yes,
    "no": QMessageBox.No,
    "cancel": QMessageBox.Cancel,
}

#: ``QMessageBox.exec_()`` 的返回码 → 后端中立名。
_RESULT_NAMES = {
    int(QMessageBox.Yes): "yes",
    int(QMessageBox.Ok): "ok",
    int(QMessageBox.No): "no",
    int(QMessageBox.Cancel): "cancel",
    int(QMessageBox.Escape): "escape",
}


__all__ = [
    "ICON_CRITICAL",
    "ICON_INFORMATION",
    "ICON_QUESTION",
    "ICON_WARNING",
    "QtMessageBoxHost",
    "ask_confirmation",
    "show_message",
]
