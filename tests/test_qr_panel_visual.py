from __future__ import annotations

import io
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore
from PIL import Image
from PyQt5.QtWidgets import QApplication

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from lib.core.graphics.application_visuals import (
    build_qr_panel_visual,
    qr_panel_action_text,
    qr_panel_size,
    resolve_qr_panel_layout,
)
from lib.core.graphics.commands import TextCommand
from lib.core.graphics.types import Color
from lib.core.layer_manager import cleanup_layer_manager
from lib.script.ui.qr_dialog_base import BaseQrDialog


class QrPanelVisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        cleanup_layer_manager()

    @staticmethod
    def _png() -> bytes:
        output = io.BytesIO()
        Image.new("RGB", (12, 12), (20, 40, 60)).save(output, format="PNG")
        return output.getvalue()

    def test_qt_dialog_consumes_shared_layout_resource_and_batch(self):
        dialog = BaseQrDialog(
            title="扫码登录",
            status="等待扫码",
            action_text="关闭窗口",
            placeholder_text="二维码加载中",
        )
        try:
            self.assertEqual((dialog.width(), dialog.height()), qr_panel_size())
            dialog._set_qr_pixmap_from_bytes(self._png(), clear_when_none=True)
            self.assertIsNotNone(dialog._qr_resource)

            layout = resolve_qr_panel_layout((dialog.width(), dialog.height()))
            _, _, qr_rect, _, action_rect = dialog._content_rects()
            self.assertEqual(qr_rect.x(), int(round(layout.qr_rect.x)))
            self.assertEqual(action_rect.y(), int(round(layout.action_rect.y)))

            visual = dialog._build_panel_visual()
            self.assertEqual(visual.batch.commands[0].fill.red, 0)
            self.assertEqual(visual.batch.commands[1].fill.green, 216)
            self.assertEqual(visual.batch.commands[2].fill.red, 255)
        finally:
            dialog.close()

    def test_qr_action_button_states_are_shared(self):
        normal = build_qr_panel_visual(
            "扫码登录", "等待扫码", "准备中", action_text="关闭窗口"
        )
        hover = build_qr_panel_visual(
            "扫码登录", "等待扫码", "准备中", action_text="关闭窗口", action_state="hover"
        )
        pressed = build_qr_panel_visual(
            "扫码登录", "等待扫码", "准备中", action_text="关闭窗口", action_state="pressed"
        )
        disabled = build_qr_panel_visual(
            "扫码登录", "等待扫码", "准备中", action_text="关闭窗口", action_enabled=False
        )
        self.assertEqual(normal.action_rect, resolve_qr_panel_layout().action_rect)
        self.assertEqual(len(normal.batch.commands), len(hover.batch.commands))
        self.assertEqual(hover.batch.commands[-2].fill, Color(255, 200, 210))
        self.assertEqual(pressed.batch.commands[-2].fill, Color(255, 170, 190))
        self.assertNotEqual(normal.batch.commands[-2].fill, hover.batch.commands[-2].fill)
        self.assertEqual(disabled.batch.commands[-1].alpha, 0.55)
        self.assertIsInstance(normal.batch.commands[-1], TextCommand)
        self.assertEqual(qr_panel_action_text("generic-login"), "关闭窗口")
        self.assertEqual(qr_panel_action_text("music-login"), "退出扫码")


if __name__ == "__main__":
    unittest.main()
