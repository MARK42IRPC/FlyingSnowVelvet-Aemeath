"""元宝二维码登录面板：自动收起与穿透切换都走基类宿主能力。"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from lib.script.ui.yuanbao_login_dialog import YuanbaoLoginDialog
from lib.script.ui.qr_dialog_base import BaseQrDialog


class _FakeEvent:
    def __init__(self, enabled):
        self.data = {"enabled": enabled}


class YuanbaoLoginDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_auto_close_timer_comes_from_the_base_host(self):
        dialog = YuanbaoLoginDialog()
        try:
            self.assertIsInstance(dialog, BaseQrDialog)
            self.assertIs(dialog._auto_close_timer.parent(), dialog)
            self.assertTrue(dialog._auto_close_timer.isSingleShot())
        finally:
            dialog.close()

    def test_hide_stops_the_auto_close_timer(self):
        dialog = YuanbaoLoginDialog()
        try:
            dialog._auto_close_timer.start(60_000)
            self.assertTrue(dialog._auto_close_timer.isActive())
            dialog.hide_dialog()  # 未显示 → 仍会先停表
            self.assertFalse(dialog._auto_close_timer.isActive())
        finally:
            dialog.close()

    def test_clickthrough_toggle_uses_the_shared_helper(self):
        dialog = YuanbaoLoginDialog()
        try:
            dialog._on_clickthrough_toggle(_FakeEvent(True))
            self.assertTrue(dialog.testAttribute(Qt.WA_TransparentForMouseEvents))
            dialog._on_clickthrough_toggle(_FakeEvent(False))
            self.assertFalse(dialog.testAttribute(Qt.WA_TransparentForMouseEvents))
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
