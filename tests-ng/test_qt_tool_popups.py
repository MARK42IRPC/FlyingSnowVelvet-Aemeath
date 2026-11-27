"""Exercise the shared QR popup through the three product entry points."""

from __future__ import annotations

import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests_ng.support.qt import app

from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QPushButton

from config.config import UI
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.render.layers import WindowLayer, get_layer_manager
from lib.core.render.visuals.application_visuals import qr_image_side, qr_panel_size
from lib.core.render.visuals.workbench_tokens import get_workbench_tokens
from lib.script.ui.cloudmusic_login_dialog import (
    cleanup_cloudmusic_login_dialog,
    get_cloudmusic_login_dialog,
    init_cloudmusic_login_dialog,
)
from lib.script.ui.qq_group_dialog import QQGroupDialog
from lib.script.ui.yuanbao_login_dialog import (
    cleanup_yuanbao_login_dialog,
    init_yuanbao_login_dialog,
)


class QrPopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()
        cls.png = cls._make_image((18, 26, 35))

    @staticmethod
    def _make_image(color: tuple[int, int, int]) -> bytes:
        image = Image.new("RGB", (48, 24), color)
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def setUp(self):
        self.center = get_event_center()
        self.addCleanup(self.app.processEvents)
        self.addCleanup(cleanup_cloudmusic_login_dialog)
        self.addCleanup(cleanup_yuanbao_login_dialog)

    def _emit(self, event_type, **data):
        self.center.publish(Event(event_type, data))
        self.app.processEvents()

    def _finish_fade(self, dialog):
        animation = dialog._spec_window._opacity_animation
        animation.setCurrentTime(animation.duration())
        self.app.processEvents()

    def test_music_login_renders_shared_shell_and_refresh_countdown_in_both_themes(self):
        with patch.dict(UI, {"workbench_light_theme": False}):
            dialog = init_cloudmusic_login_dialog()
            self._emit(EventType.MUSIC_LOGIN_QR_SHOW, qr_png=self.png, title="网易云扫码登录")
            self._emit(EventType.MUSIC_LOGIN_QR_STATUS, status="等待扫码...", refresh_left=9)
            self._finish_fade(dialog)
            window = dialog._spec_window
            self.assertTrue(window.spec.shared_popup)
            self.assertEqual((dialog.width(), dialog.height()), qr_panel_size())
            self.assertEqual(window.find("header").text(), "网易云扫码登录")
            body = window.find("body")
            self.assertEqual(body.objectName(), "AnnouncementBody")
            entry = window.find("action")
            self.assertIsInstance(entry, QPushButton)
            self.assertEqual(entry.text(), "退出扫码")
            status = window.find("status")
            self.assertEqual(status.text(), "等待扫码...\n二维码将于 9s 后刷新")
            metrics = status.fontMetrics()
            self.assertGreaterEqual(status.height(), metrics.height() + metrics.lineSpacing())
            self.assertEqual(status.textFormat(), Qt.PlainText)
            qr = dialog._qr_image
            self.assertTrue(body.rect().contains(qr.geometry()))
            self.assertEqual(
                min(qr.width(), qr.height()),
                qr_image_side() if qr.width() == qr_image_side() else qr.width(),
            )
            self.assertGreater(qr.width(), 0)
            self.assertGreater(qr.height(), 0)

            for mode in ("dark", "light"):
                with self.subTest(mode=mode):
                    UI["workbench_light_theme"] = mode == "light"
                    self._emit(EventType.CONFIG_UPDATED, values={"UI": {"workbench_light_theme": mode == "light"}})
                    colors = get_workbench_tokens(mode)
                    pixels = dialog.grab().toImage()
                    self.assertEqual(pixels.pixelColor(0, 0).name(), colors["border_strong"])
                    self.assertEqual(pixels.pixelColor(5, 5).name(), colors["canvas"])
                    self.assertIn(colors["surface"], dialog.styleSheet())
                    image_pixels = qr.grab().toImage()
                    self.assertEqual(image_pixels.pixelColor(1, 1).name(), "#ffffff")
                    self.assertEqual(img_center_color(image_pixels), "#121a23")

    def test_music_no_activation_minimize_cancel_and_external_hide(self):
        dialog = init_cloudmusic_login_dialog()
        cancellations = []
        self.center.subscribe(EventType.MUSIC_LOGIN_CANCEL_REQUEST, cancellations.append)
        self.addCleanup(self.center.unsubscribe, EventType.MUSIC_LOGIN_CANCEL_REQUEST, cancellations.append)
        self._emit(EventType.MUSIC_LOGIN_QR_SHOW, qr_png=self.png)
        self.assertTrue(dialog.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.assertTrue(dialog.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertFalse(dialog.close())
        dialog.hide()
        self.app.processEvents()
        self.assertTrue(dialog.isVisible())

        self._emit(EventType.UI_CLICKTHROUGH_TOGGLE, enabled=True)
        self.assertTrue(dialog.testAttribute(Qt.WA_TransparentForMouseEvents))
        self._emit(EventType.UI_CLICKTHROUGH_TOGGLE, enabled=False)
        self.assertFalse(dialog.testAttribute(Qt.WA_TransparentForMouseEvents))

        dialog._spec_window.find("minimize").click()
        self._finish_fade(dialog)
        self.assertFalse(dialog.isVisible())
        self.assertEqual(cancellations, [])
        for node_id in ("action", "close", "action"):
            self._emit(EventType.MUSIC_LOGIN_QR_SHOW, qr_png=self.png)
            dialog._spec_window.find(node_id).click()
            self.assertTrue(dialog.isVisible())
            self._emit(EventType.MUSIC_LOGIN_QR_HIDE)
            self._finish_fade(dialog)
            self.assertFalse(dialog.isVisible())
        self.assertEqual(len(cancellations), 3)

    def test_music_cleanup_during_external_hide_does_not_reopen_the_singleton(self):
        dialog = init_cloudmusic_login_dialog()
        self._emit(EventType.MUSIC_LOGIN_QR_SHOW, qr_png=self.png)
        layer = int(WindowLayer.DIALOG)
        self.assertTrue(any(r[0] == layer and r[3] == "CloudMusicLoginDialog" for r in get_layer_manager().snapshot()))
        dialog.hide()
        cleanup_cloudmusic_login_dialog()
        self.app.processEvents()
        self.assertIsNone(get_cloudmusic_login_dialog())
        self.assertFalse(any(r[3] == "CloudMusicLoginDialog" for r in get_layer_manager().snapshot()))
        self.assertFalse(dialog._spec_window.is_requested_visible())

    def test_yuanbao_dismissal_and_success_timer_remain_product_owned(self):
        dialog = init_yuanbao_login_dialog()
        self._emit(EventType.YUANBAO_LOGIN_QR_SHOW, qr_png=self.png)
        dialog._spec_window.find("action").click()
        self._finish_fade(dialog)
        self.assertFalse(dialog.isVisible())
        self._emit(EventType.YUANBAO_LOGIN_QR_STATUS, logged_in=True, title="元宝扫码登录", qr_png=self.png)
        self.assertIn("登录成功", dialog._spec_window.find("status").text())
        self.assertTrue(dialog._auto_close_timer.isActive())
        self._emit(EventType.YUANBAO_LOGIN_QR_HIDE)
        self.assertTrue(dialog._spec_window.is_requested_visible())
        dialog._auto_close_timer.timeout.emit()
        self._finish_fade(dialog)
        self.assertFalse(dialog.isVisible())
        self.assertFalse(dialog._auto_close_timer.isActive())

    def test_qq_group_reloads_local_image_and_restores_placeholder_when_missing(self):
        with TemporaryDirectory() as directory:
            sample = Path(directory) / "QQqrc.png"
            dialog = QQGroupDialog(sample)
            self.addCleanup(dialog.cleanup)
            dialog.show_dialog()
            self.assertIsNone(dialog._qr_image.resource)
            sample.write_bytes(self.png)
            dialog.show_dialog()
            first_id = dialog._qr_image.resource.resource_id
            self.assertEqual(img_center_color(dialog._qr_image.grab().toImage()), "#121a23")
            sample.write_bytes(self._make_image((54, 43, 32)))
            dialog.show_dialog()
            self.assertNotEqual(dialog._qr_image.resource.resource_id, first_id)
            self.assertEqual(img_center_color(dialog._qr_image.grab().toImage()), "#362b20")
            sample.unlink()
            dialog.show_dialog()
            self.assertIsNone(dialog._qr_image.resource)
            dialog._spec_window.find("close").click()
            self._finish_fade(dialog)
            self.assertFalse(dialog.isVisible())


def img_center_color(image) -> str:
    return image.pixelColor(image.width() // 2, image.height() // 2).name()
