"""Shared announcement-style confirmation / notice window semantics."""

# ruff: noqa: E402 - Qt plugin variables must be set before PyQt imports.

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"))
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QPushButton

from lib.core.render.backends.qt.widgets import message_box_host
from lib.core.render.backends.qt.widgets import spec_host as spec_host_module
from lib.script.ui.confirm_dialog import (
    CONFIRM_DIALOG_OBJECT_NAME,
    DESTRUCTIVE_BUTTON_OBJECT_NAME,
    PRIMARY_BUTTON_OBJECT_NAME,
    ask_confirmation,
    confirm_dialog_stylesheet,
    show_message,
)
from lib.core.render.visuals.message_box import message_box_window_spec
from lib.core.render.visuals.workbench_tokens import get_workbench_tokens


class ConfirmDialogStyleTests(unittest.TestCase):
    def test_stylesheet_uses_the_announcement_shell_and_palette(self):
        stylesheet = confirm_dialog_stylesheet("light")
        tokens = get_workbench_tokens("light")

        self.assertIn("QFrame#AnnouncementHeaderAccent", stylesheet)
        self.assertIn("QTextBrowser#AnnouncementBody", stylesheet)
        self.assertIn('QPushButton[windowActionRole="danger"]', stylesheet)
        for token in ("canvas", "surface", "pink", "cyan", "danger"):
            self.assertIn(tokens[token], stylesheet)

    def test_stylesheet_follows_the_workbench_theme(self):
        dark = confirm_dialog_stylesheet("dark")
        light = confirm_dialog_stylesheet("light")
        self.assertNotEqual(dark, light)
        self.assertIn(get_workbench_tokens("dark")["canvas"], dark)

    def test_window_spec_uses_the_shared_announcement_shell(self):
        spec = message_box_window_spec(
            title="卸载桌宠", text="确定卸载吗？", primary_text="卸载",
            cancel_text="取消", destructive=True,
        )
        self.assertEqual(spec.object_name, CONFIRM_DIALOG_OBJECT_NAME)
        self.assertEqual(spec.layer, "dialog")
        self.assertTrue(spec.modal)
        self.assertEqual(spec.default_button_id, "cancel")
        self.assertEqual(spec.close_semantic, "cancel")
        self.assertTrue(spec.border_frame)


class ConfirmDialogBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_explicit_confirm_reports_yes(self):
        created = []
        original = message_box_host.build_spec_window

        def build(*args, **kwargs):
            window = original(*args, **kwargs)
            created.append(window)
            window.exec_modal = lambda **_kwargs: "accept"
            return window

        with patch.object(message_box_host, "build_spec_window", side_effect=build):
            self.assertTrue(ask_confirmation(None, title="卸载桌宠", text="确定吗？"))

        self.assertEqual(created[0].button_spec("accept").text, "确定")
        created[0].cleanup()
        self.app.processEvents()

    def test_cancel_or_close_never_confirms(self):
        original = message_box_host.build_spec_window
        for result in ("cancel", ""):
            with self.subTest(result=result), patch.object(
                message_box_host, "build_spec_window", wraps=original
            ) as build:
                window = build.return_value
                # The return value is a MagicMock; patch the constructed object via side effect below.
                del window
                made = []

                def create(*args, **kwargs):
                    value = original(*args, **kwargs)
                    value.exec_modal = lambda **_kwargs: result
                    made.append(value)
                    return value

                with patch.object(message_box_host, "build_spec_window", side_effect=create):
                    self.assertFalse(ask_confirmation(None, title="确认", text="正文"))
                made[0].cleanup()
                self.app.processEvents()

    def test_destructive_confirmation_has_default_cancel_and_danger_role(self):
        made = []
        original = message_box_host.build_spec_window

        def create(*args, **kwargs):
            value = original(*args, **kwargs)
            value.exec_modal = lambda **_kwargs: "cancel"
            made.append(value)
            return value

        with patch.object(message_box_host, "build_spec_window", side_effect=create):
            self.assertFalse(ask_confirmation(
                None, title="删除", text="确定删除吗？", confirm_text="删除", destructive=True
            ))
        window = made[0]
        confirm = window.find("primary")
        cancel = window.find("cancel")
        self.assertEqual(confirm.objectName(), DESTRUCTIVE_BUTTON_OBJECT_NAME)
        self.assertEqual(confirm.property("windowActionRole"), "danger")
        self.assertTrue(cancel.isDefault())
        self.assertFalse(confirm.autoDefault())
        window.cleanup()
        self.app.processEvents()

    def test_notice_uses_one_announcement_style_primary_button(self):
        made = []
        original = message_box_host.build_spec_window

        def create(*args, **kwargs):
            value = original(*args, **kwargs)
            value.exec_modal = lambda **_kwargs: "accept"
            made.append(value)
            return value

        with patch.object(message_box_host, "build_spec_window", side_effect=create):
            show_message(None, title="提示", text="发生了问题")
        window = made[0]
        buttons = window.widget.findChildren(QPushButton)
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].text(), "知道了")
        self.assertEqual(buttons[0].objectName(), PRIMARY_BUTTON_OBJECT_NAME)
        window.cleanup()
        self.app.processEvents()

    def test_modal_registers_and_releases_dialog_layer(self):
        registered, unregistered = [], []

        class LayerManager:
            def register(self, widget, layer, **kwargs):
                registered.append((widget, layer, kwargs))

            def enforce_burst(self):
                pass

            def unregister(self, widget):
                unregistered.append(widget)

        made = []
        original = message_box_host.build_spec_window

        def create(*args, **kwargs):
            value = original(*args, **kwargs)
            made.append(value)
            QTimer.singleShot(0, lambda: value.find("cancel").click())
            return value

        with patch.object(spec_host_module, "get_layer_manager", return_value=LayerManager()), \
                patch.object(message_box_host, "build_spec_window", side_effect=create):
            self.assertFalse(ask_confirmation(None, title="确认", text="正文"))
        self.assertEqual(registered[0][1], spec_host_module.WindowLayer.DIALOG)
        self.assertIn(made[0].widget, unregistered)
        made[0].cleanup()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
