"""共享确认/提示弹窗：外观契约与按钮语义。"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication, QMessageBox

from lib.script.ui import confirm_dialog
from lib.script.ui.confirm_dialog import (
    CONFIRM_DIALOG_OBJECT_NAME,
    DESTRUCTIVE_BUTTON_OBJECT_NAME,
    PRIMARY_BUTTON_OBJECT_NAME,
    ask_confirmation,
    confirm_dialog_stylesheet,
    show_message,
)
from lib.script.workbench.theme import get_workbench_colors


class ConfirmDialogStyleTests(unittest.TestCase):
    """样式表本身只需要覆盖关键选择器，不需要构造真窗口。"""

    def test_stylesheet_paints_the_dialog_surface_and_buttons(self):
        stylesheet = confirm_dialog_stylesheet("light")
        colors = get_workbench_colors("light")

        self.assertIn(f"QMessageBox#{CONFIRM_DIALOG_OBJECT_NAME}", stylesheet)
        self.assertIn(colors.canvas, stylesheet)
        self.assertIn(colors.text, stylesheet)
        self.assertIn(colors.surface_raised, stylesheet)
        self.assertIn(DESTRUCTIVE_BUTTON_OBJECT_NAME, stylesheet)
        self.assertIn(PRIMARY_BUTTON_OBJECT_NAME, stylesheet)

    def test_stylesheet_follows_the_workbench_theme(self):
        dark = confirm_dialog_stylesheet("dark")
        light = confirm_dialog_stylesheet("light")

        self.assertNotEqual(dark, light)
        self.assertIn(get_workbench_colors("dark").canvas, dark)


class ConfirmDialogBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _spy(created):
        """记录真实构造出来的对话框，同时保留原来的构造行为。

        ``exec_`` 是实例方法，直接用 ``side_effect`` 会先绑上实例，拿不到窗口本身。
        """
        original = confirm_dialog._build_dialog

        def build(*args, **kwargs):
            dialog = original(*args, **kwargs)
            created.append(dialog)
            return dialog

        return build

    def test_confirmation_reports_the_confirm_button(self):
        with patch.object(confirm_dialog.QMessageBox, "exec_", return_value=QMessageBox.Yes):
            self.assertTrue(
                ask_confirmation(None, title="卸载桌宠", text="确定卸载桌宠吗？")
            )

    def test_confirmation_reports_cancel(self):
        with patch.object(confirm_dialog.QMessageBox, "exec_", return_value=QMessageBox.Cancel):
            self.assertFalse(ask_confirmation(None, title="卸载桌宠", text="确定卸载桌宠吗？"))

    def test_destructive_confirmation_marks_the_button_and_escapes_to_cancel(self):
        created = []
        with patch.object(confirm_dialog, "_build_dialog", self._spy(created)), patch.object(
            confirm_dialog.QMessageBox, "exec_", return_value=QMessageBox.Cancel
        ):
            ask_confirmation(
                None,
                title="卸载桌宠",
                text="确定卸载桌宠吗？",
                confirm_text="卸载",
                destructive=True,
            )

        box = created[0]
        try:
            confirm_button = box.button(QMessageBox.Yes)
            self.assertEqual(confirm_button.text(), "卸载")
            self.assertEqual(confirm_button.objectName(), DESTRUCTIVE_BUTTON_OBJECT_NAME)
            cancel_button = box.button(QMessageBox.Cancel)
            self.assertEqual(cancel_button.text(), "取消")
            self.assertIs(box.escapeButton(), cancel_button)
            self.assertIs(box.defaultButton(), cancel_button)
            self.assertEqual(box.objectName(), CONFIRM_DIALOG_OBJECT_NAME)
        finally:
            box.deleteLater()
            self.app.processEvents()

    def test_confirmation_returns_false_when_closed_without_a_choice(self):
        with patch.object(confirm_dialog.QMessageBox, "exec_", return_value=0):
            self.assertFalse(ask_confirmation(None, title="卸载桌宠", text="确定卸载桌宠吗？"))

    def test_confirmation_releases_its_layer_registration(self):
        registered = []
        with patch.object(
            confirm_dialog,
            "get_layer_manager",
            side_effect=lambda: type(
                "Stub",
                (),
                {
                    "register": lambda _self, box, layer, **kwargs: registered.append(box),
                    "unregister": lambda _self, box: registered.remove(box),
                },
            )(),
        ), patch.object(confirm_dialog.QMessageBox, "exec_", return_value=QMessageBox.Cancel):
            ask_confirmation(None, title="卸载桌宠", text="确定卸载桌宠吗？")

        self.assertEqual(registered, [])

    def test_notice_uses_a_single_primary_button(self):
        created = []
        with patch.object(confirm_dialog, "_build_dialog", self._spy(created)), patch.object(
            confirm_dialog.QMessageBox, "exec_", return_value=QMessageBox.Ok
        ):
            show_message(None, title="提示", text="源码工作区没有安装版卸载程序。")

        box = created[0]
        try:
            self.assertEqual(box.text(), "源码工作区没有安装版卸载程序。")
            ok_button = box.button(QMessageBox.Ok)
            self.assertEqual(ok_button.text(), "知道了")
            self.assertEqual(ok_button.objectName(), PRIMARY_BUTTON_OBJECT_NAME)
        finally:
            box.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
