"""QR / 更新 / 下载 / 公告浮窗共享工作台外壳的视觉与交互验证。"""

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

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication, QWidget

from config.config import UI
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.layer_manager import cleanup_layer_manager
from lib.script.ui.announcement_dialog import DesktopPetAnnouncementDialog
from lib.script.ui.qr_dialog_base import BaseQrDialog
from lib.script.ui.update_dialog import DesktopPetUpdateDialog
from lib.script.ui.voice_package_installer import VoicePackageInstallerDialog
from lib.script.ui.workbench_floating import (
    WorkbenchFloatingWindow,
    floating_window_stylesheet,
    is_workbench_theme_change,
)
from lib.script.workbench.theme import get_workbench_colors


class _FakeFloatingWindow(WorkbenchFloatingWindow):
    """Stand-in window that records its collapse path."""

    def __init__(self) -> None:
        super().__init__()
        self.collapsed = 0

    def hide_dialog(self) -> None:
        self.collapsed += 1
        self.hide()


def _mouse(
    event_type: QEvent.Type,
    local: QPoint,
    screen: QPoint,
    button: Qt.MouseButton,
    buttons: Qt.MouseButtons,
) -> QMouseEvent:
    return QMouseEvent(event_type, local, screen, button, buttons, Qt.NoModifier)


class WorkbenchFloatingChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._original_theme = bool(UI.get("workbench_light_theme", False))

    def tearDown(self) -> None:
        UI["workbench_light_theme"] = self._original_theme
        cleanup_layer_manager()
        self.app.processEvents()

    def _set_theme(self, light: bool) -> None:
        UI["workbench_light_theme"] = bool(light)

    def _publish_theme(self) -> None:
        get_event_center().publish(
            Event(
                EventType.CONFIG_UPDATED,
                {"values": {"UI": {"workbench_light_theme": bool(UI["workbench_light_theme"])}}},
            )
        )
        self.app.processEvents()

    def test_floating_stylesheet_follows_the_live_workbench_palette(self):
        self._set_theme(False)
        dark = floating_window_stylesheet()
        dark_colors = get_workbench_colors("dark")
        self._set_theme(True)
        light = floating_window_stylesheet()
        light_colors = get_workbench_colors("light")

        self.assertNotEqual(dark, light)
        self.assertIn(dark_colors.surface, dark)
        self.assertIn(light_colors.surface, light)
        self.assertNotIn(light_colors.surface, dark)
        for stylesheet in (dark, light):
            self.assertIn("QToolButton#WorkbenchWindowButton", stylesheet)
            self.assertIn("QProgressBar::chunk", stylesheet)
            self.assertIn("QComboBox::down-arrow", stylesheet)

    def test_theme_event_repolishes_visible_window_and_stops_when_hidden(self):
        window = _FakeFloatingWindow()
        window.resize(200, 120)
        window.show()
        self.app.processEvents()
        visible_sheet = window.styleSheet()
        self.assertTrue(visible_sheet)

        self._set_theme(not self._original_theme)
        self._publish_theme()
        refreshed_sheet = window.styleSheet()
        self.assertNotEqual(refreshed_sheet, visible_sheet)

        window.hide()
        self.app.processEvents()
        self._set_theme(self._original_theme)
        self._publish_theme()
        self.assertEqual(window.styleSheet(), refreshed_sheet)
        window.deleteLater()

    def test_drag_handle_moves_the_frameless_window(self):
        window = _FakeFloatingWindow()
        window.setFixedSize(240, 140)
        handle = QWidget(window)
        handle.setGeometry(0, 0, 240, 24)
        window.attach_floating_drag_handle(handle, cursor=False)
        window.move(100, 100)
        start = window.pos()

        QApplication.sendEvent(
            handle,
            _mouse(QEvent.MouseButtonPress, QPoint(10, 10), QPoint(110, 110), Qt.LeftButton, Qt.LeftButton),
        )
        QApplication.sendEvent(
            handle,
            _mouse(QEvent.MouseMove, QPoint(40, 35), QPoint(140, 135), Qt.NoButton, Qt.LeftButton),
        )
        self.assertEqual(window.pos() - start, QPoint(30, 25))

        QApplication.sendEvent(
            handle,
            _mouse(QEvent.MouseButtonRelease, QPoint(40, 35), QPoint(140, 135), Qt.LeftButton, Qt.NoButton),
        )
        QApplication.sendEvent(
            handle,
            _mouse(QEvent.MouseMove, QPoint(80, 90), QPoint(180, 190), Qt.NoButton, Qt.LeftButton),
        )
        self.assertEqual(window.pos() - start, QPoint(30, 25))
        window.deleteLater()

    def test_minimize_uses_the_dialog_collapse_path(self):
        window = _FakeFloatingWindow()
        window.show()
        window.minimize_floating_window()
        self.assertEqual(window.collapsed, 1)

        plain = WorkbenchFloatingWindow()
        plain.show()
        plain.minimize_floating_window()
        self.assertFalse(plain.isVisible())
        plain.deleteLater()
        window.deleteLater()

    def test_theme_event_payload_detection(self):
        self.assertTrue(is_workbench_theme_change(
            Event(EventType.CONFIG_UPDATED, {"values": {"UI": {"workbench_light_theme": True}}})
        ))
        self.assertFalse(is_workbench_theme_change(
            Event(EventType.CONFIG_UPDATED, {"values": {"UI": {"other": 1}}})
        ))
        self.assertFalse(is_workbench_theme_change(Event(EventType.CONFIG_UPDATED, {})))
        self.assertFalse(is_workbench_theme_change(Event(EventType.TICK, None)))

    def test_qr_dialog_shares_chrome_and_theme(self):
        dialog = BaseQrDialog(
            title="扫码登录",
            status="等待扫码",
            action_text="关闭窗口",
            placeholder_text="二维码加载中",
        )
        try:
            self.assertIsInstance(dialog, WorkbenchFloatingWindow)
            self.assertEqual(dialog._minimize_btn.toolTip(), "最小化")
            self.assertIn(get_workbench_colors().surface, dialog.styleSheet())

            dialog._opacity.setOpacity(1.0)
            image = dialog.grab().toImage()
            tokens = get_workbench_colors()
            self.assertEqual(image.pixelColor(0, 0).name(), tokens.border_strong)
            self.assertEqual(image.pixelColor(10, 10).name(), tokens.surface)

            dialog._show_dialog()
            self.assertTrue(dialog._visible)
            dialog.minimize_floating_window()
            self.assertFalse(dialog._visible)
        finally:
            dialog.close()

    def test_update_dialog_shares_chrome_and_paints_workbench_tokens(self):
        dialog = DesktopPetUpdateDialog()
        try:
            self.assertIsInstance(dialog, WorkbenchFloatingWindow)
            self.assertEqual(dialog._minimize_btn.toolTip(), "最小化")

            dialog._opacity.setOpacity(1.0)
            image = dialog.grab().toImage()
            tokens = get_workbench_colors()
            self.assertEqual(image.pixelColor(0, 0).name(), tokens.border_strong)
            self.assertEqual(
                image.pixelColor(dialog.width() // 2, dialog.height() - 1).name(),
                tokens.border_strong,
            )

            dialog._set_busy(True)
            dialog._show_dialog()
            dialog.minimize_floating_window()
            self.assertTrue(dialog._visible)
            self.assertFalse(dialog._minimize_btn.isEnabled())

            dialog._set_busy(False)
            dialog.minimize_floating_window()
            self.assertFalse(dialog._visible)
        finally:
            dialog.deleteLater()

    def test_voice_installer_shares_chrome_and_paints_workbench_tokens(self):
        dialog = VoicePackageInstallerDialog()
        try:
            self.assertIsInstance(dialog, WorkbenchFloatingWindow)
            self.assertEqual(dialog._minimize_btn.toolTip(), "最小化")
            self.assertIn(get_workbench_colors().surface_raised, dialog.styleSheet())

            dialog._opacity.setOpacity(1.0)
            image = dialog.grab().toImage()
            tokens = get_workbench_colors()
            self.assertEqual(image.pixelColor(0, 0).name(), tokens.border_strong)

            dialog.show()
            dialog.minimize_floating_window()
            self.assertFalse(dialog._visible)
        finally:
            dialog.cleanup()
            self.app.processEvents()

    def test_announcement_dialog_shares_chrome_and_collapses(self):
        dialog = DesktopPetAnnouncementDialog()
        try:
            self.assertIsInstance(dialog, WorkbenchFloatingWindow)
            self.assertEqual(dialog._minimize_button.toolTip(), "最小化")
            self.assertIn(get_workbench_colors().canvas, dialog.styleSheet())

            dialog.show_loading()
            self.assertTrue(dialog.wants_visible())
            dialog.minimize_floating_window()
            self.assertFalse(dialog.wants_visible())
        finally:
            dialog.cleanup()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
