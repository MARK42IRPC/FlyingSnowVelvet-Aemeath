"""Backend-neutral QR image and shared popup chrome guarantees."""

from __future__ import annotations

import io
import unittest

from PIL import Image

from config.scale import set_draw_scale, set_user_scale
from lib.core.render.layers import Layer
from lib.core.render.visuals.application_visuals import (
    build_qr_image_batch,
    build_qr_panel_visual,
    decode_panel_image,
    qr_image_side,
    qr_panel_action_text,
    qr_panel_size,
    resolve_qr_panel_layout,
)
from lib.core.render.visuals.commands import RectCommand, SpriteCommand, TextCommand
from lib.core.render.visuals.types import Point, Rect, Size
from lib.core.render.visuals.window_spec import ButtonSpec


class QrPopupPresenterTests(unittest.TestCase):
    def setUp(self):
        self._original_scale = set_draw_scale.__globals__["_draw_scale"]
        self._original_user_scale = set_draw_scale.__globals__["_user_scale"]
        set_draw_scale(1.0)
        set_user_scale(1.0)

    def tearDown(self):
        set_draw_scale(self._original_scale)
        set_user_scale(self._original_user_scale)

    @staticmethod
    def _png(size=(20, 10), color=(30, 40, 50)):
        buffer = io.BytesIO()
        Image.new("RGB", size, color).save(buffer, format="PNG")
        return buffer.getvalue()

    def test_default_native_shell_and_scan_field_geometry_match_qt_body(self):
        self.assertEqual(qr_panel_size(), (320, 430))
        layout = resolve_qr_panel_layout()
        self.assertEqual(layout.size, Size(320, 430))
        self.assertEqual(layout.qr_rect.width, qr_image_side())
        self.assertEqual(layout.qr_rect.height, qr_image_side())
        self.assertEqual(layout.title_rect.height, 42)
        self.assertEqual(layout.status_rect.y, layout.qr_rect.bottom + 8)
        self.assertEqual(layout.action_rect.width, 132)
        self.assertLessEqual(layout.status_rect.bottom, layout.action_rect.y)

    def test_scale_preserves_qr_and_action_geometry_without_overlap(self):
        set_draw_scale(1.5)
        layout = resolve_qr_panel_layout()
        self.assertEqual((layout.size.width, layout.size.height), (480, 645))
        self.assertEqual(layout.qr_rect.width, qr_image_side())
        self.assertEqual(layout.qr_rect.height, qr_image_side())
        self.assertLessEqual(layout.status_rect.bottom, layout.action_rect.y)

    def test_shared_shell_wraps_qr_content_and_retains_action_semantics(self):
        image = decode_panel_image(self._png(), resource_prefix="qr-test")
        visual = build_qr_panel_visual(
            "扫码登录", "等待扫码", "加载中", image, action_text="退出扫码"
        )
        commands = visual.batch.commands
        outer, canvas, panel = commands[:3]
        self.assertEqual((visual.size.width, visual.size.height), qr_panel_size())
        self.assertIsInstance(outer, RectCommand)
        self.assertIsInstance(canvas, RectCommand)
        self.assertIsInstance(panel, RectCommand)
        self.assertGreater(panel.radius, 0)
        self.assertTrue(any(
            isinstance(command, SpriteCommand)
            and command.resource_id == image.resource_id
            and command.target_size == Size(qr_image_side(), qr_image_side() // 2)
            for command in commands
        ))
        self.assertTrue(any(
            isinstance(command, TextCommand) and command.text == "等待扫码"
            for command in commands
        ))
        self.assertEqual(visual.action_rect, resolve_qr_panel_layout().action_rect)
        semantics = [
            node.semantic for node in __import__(
                "lib.core.render.visuals.window_spec", fromlist=["button_specs"]
            ).button_specs(__import__(
                "lib.core.render.visuals.announcement_chrome", fromlist=["announcement_shell_spec"]
            ).announcement_shell_spec(
                object_name="qr", title="扫码登录", body=__import__(
                    "lib.core.render.visuals.announcement_chrome", fromlist=["announcement_panel_body"]
                ).announcement_panel_body(),
                actions=(ButtonSpec("退出扫码", "action"),), close_action="close",
            ))
        ]
        self.assertEqual(semantics, ["close", "action"])
        self.assertEqual(qr_panel_action_text("music-login"), "退出扫码")
        self.assertEqual(qr_panel_action_text("yuanbao-login"), "关闭窗口")

    def test_image_only_batch_has_scan_background_and_no_native_header(self):
        image = decode_panel_image(self._png(), resource_prefix="qr-content")
        batch = build_qr_image_batch(Rect(12, 8, 200, 180), "准备中", image)
        self.assertEqual(len(batch.commands), 2)
        self.assertIsInstance(batch.commands[0], RectCommand)
        self.assertEqual(batch.commands[0].fill, type(batch.commands[0].fill)(255, 255, 255))
        sprite = batch.commands[1]
        self.assertIsInstance(sprite, SpriteCommand)
        self.assertEqual(sprite.position, Point(12, 48))
        self.assertEqual(sprite.target_size, Size(200, 100))
        self.assertTrue(batch.resource_revisions)

    def test_empty_image_batch_is_pure_placeholder_and_never_loads_qt(self):
        batch = build_qr_image_batch(Rect(0, 0, 240, 240), "二维码加载中", None)
        self.assertEqual(len(batch.commands), 1)
        self.assertIsInstance(batch.commands[0], TextCommand)
        self.assertEqual(batch.commands[0].text, "二维码加载中")
        self.assertEqual(batch.commands[0].layer, int(Layer.DIALOG))
        import sys

        self.assertFalse(any(name.startswith("PyQt5") for name in sys.modules))


if __name__ == "__main__":
    unittest.main()
