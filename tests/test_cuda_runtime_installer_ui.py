import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore
from PyQt5.QtCore import QPoint

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication

from config.config import UI
from lib.core.cuda_runtime_installer import CudaRuntimeInstallResult
from lib.core.graphics.announcement_visuals import (
    ANNOUNCEMENT_DARK_COLORS,
    ANNOUNCEMENT_LIGHT_COLORS,
)
from lib.core.qt_bridge.font import get_ui_font_family
from lib.script.ui import cuda_runtime_installer as installer_ui
from lib.script.ui.cuda_runtime_installer import CudaRuntimeInstallerDialog


class CudaRuntimeInstallerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_shows_effect_sizes_progress_and_required_actions(self):
        dialog = CudaRuntimeInstallerDialog()
        try:
            self.assertLessEqual(dialog.width(), 520)
            self.assertLessEqual(dialog.height(), 440)
            self.assertIn("语义解码", dialog._effect.text())
            self.assertIn("显存占用", dialog._effect.text())
            self.assertIn("下载大小", dialog._size_detail.text())
            self.assertIn("安装占用", dialog._size_detail.text())
            self.assertIn("可用空间", dialog._size_detail.text())
            self.assertEqual(dialog._primary.text(), "开始安装")
            self.assertEqual(dialog._cancel.text(), "取消")
            self.assertEqual(dialog._download_bar.objectName(), "CudaRuntimeDownloadProgress")
            self.assertEqual(dialog._install_bar.objectName(), "CudaRuntimeInstallProgress")
        finally:
            dialog.cleanup()
            self.app.processEvents()

    def test_start_uses_interactive_io_and_cancel_stops_installer(self):
        class HoldingHub:
            @staticmethod
            def submit_interactive_io(_func):
                return object()

        runtime_installer = Mock()
        with patch.object(
            installer_ui, "create_cuda_runtime_installer", return_value=runtime_installer
        ), patch.object(installer_ui, "get_compute_hub", return_value=HoldingHub()):
            dialog = CudaRuntimeInstallerDialog()
            try:
                dialog._on_primary()
                self.assertTrue(dialog.is_busy())
                self.assertEqual(dialog._cancel.text(), "取消安装")
                self.assertTrue(dialog._primary.isHidden())

                dialog._on_cancel()
                runtime_installer.cancel.assert_called_once_with()
                self.assertFalse(dialog._cancel.isEnabled())
                self.assertIn("正在取消", dialog._status.text())
            finally:
                dialog.cleanup()
                self.app.processEvents()

    def test_installer_creation_failure_restores_retry_actions(self):
        with patch.object(
            installer_ui,
            "create_cuda_runtime_installer",
            side_effect=RuntimeError("cannot inspect package"),
        ):
            dialog = CudaRuntimeInstallerDialog()
            try:
                dialog._on_primary()
                self.assertFalse(dialog.is_busy())
                self.assertIn("安装器初始化失败", dialog._status.text())
                self.assertEqual(dialog._primary.text(), "重新安装")
                self.assertEqual(dialog._cancel.text(), "关闭")
            finally:
                dialog.cleanup()
                self.app.processEvents()

    def test_failure_and_cancel_clear_completed_state_for_retry(self):
        dialog = CudaRuntimeInstallerDialog()
        try:
            dialog._completed = True
            dialog._on_install_error("校验失败")
            self.assertFalse(dialog._completed)
            self.assertEqual(dialog._primary.text(), "重新安装")

            dialog._completed = True
            dialog._on_install_cancelled()
            self.assertFalse(dialog._completed)
            self.assertEqual(dialog._primary.text(), "重新安装")
        finally:
            dialog.cleanup()
            self.app.processEvents()

    def test_cleanup_clears_busy_state_and_is_idempotent(self):
        dialog = CudaRuntimeInstallerDialog()
        dialog._busy = True
        dialog.cleanup()
        try:
            self.assertFalse(dialog.is_busy())
            dialog.cleanup()
        finally:
            self.app.processEvents()

    def test_progress_is_monotonic_and_success_switches_to_close(self):
        dialog = CudaRuntimeInstallerDialog()
        try:
            dialog._apply_progress("install", 720, 1000, "正在校验")
            dialog._apply_progress("install", 650, 1000, "延迟进度")
            self.assertEqual(dialog._install_bar.value(), 720)

            result = CudaRuntimeInstallResult(Path("runtime"), 1, 2, "bundle")
            completed = []
            dialog.install_succeeded.connect(completed.append)
            dialog._busy = True
            dialog._on_install_success(result)
            self.assertFalse(dialog.is_busy())
            self.assertEqual(dialog._primary.text(), "关闭")
            self.assertTrue(dialog._cancel.isHidden())
            self.assertEqual(completed, [result])
        finally:
            dialog.cleanup()
            self.app.processEvents()

    def test_light_theme_matches_announcement_palette_and_embedded_font_pixels(self):
        original_theme = UI["workbench_light_theme"]
        UI["workbench_light_theme"] = True
        dialog = CudaRuntimeInstallerDialog()
        try:
            # The opacity effect starts at zero; force a stable frame for offscreen capture.
            dialog._opacity.setOpacity(1.0)
            dialog.show()
            self.app.processEvents()

            image = dialog.grab().toImage()
            colors = ANNOUNCEMENT_LIGHT_COLORS

            def pixel(x, y):
                return image.pixelColor(int(x), int(y)).getRgb()[:3]

            def rgb(color):
                return (color.red, color.green, color.blue)

            self.assertEqual(pixel(0, 0), rgb(colors["border_strong"]))
            self.assertEqual(pixel(1, 1), rgb(colors["canvas"]))

            content = dialog._content.geometry()
            self.assertEqual(pixel(content.right() - 2, content.top() + 2), rgb(colors["surface"]))

            progress = dialog._download_bar.geometry()
            progress_sample = dialog._download_bar.mapTo(
                dialog,
                QPoint(progress.width() * 3 // 4, progress.height() // 2),
            )
            self.assertEqual(
                pixel(progress_sample.x(), progress_sample.y()),
                rgb(colors["surface_raised"]),
            )

            accent = dialog._header_accent.geometry()
            self.assertEqual(pixel(accent.center().x(), accent.center().y()), rgb(colors["pink"]))

            primary = dialog._primary.geometry()
            self.assertEqual(
                pixel(primary.left() + 4, primary.center().y()),
                rgb(colors["pink"]),
            )

            family = get_ui_font_family()
            for widget in (
                dialog,
                dialog._title,
                dialog._effect,
                dialog._status,
                dialog._cancel,
                dialog._primary,
                dialog._download_bar,
                dialog._install_bar,
            ):
                self.assertEqual(widget.font().family(), family)

            # Switching the same dialog to dark mode must repaint from the shared palette.
            UI["workbench_light_theme"] = False
            dialog._apply_style()
            self.app.processEvents()
            dark_image = dialog.grab().toImage()
            self.assertEqual(
                dark_image.pixelColor(0, 0).getRgb()[:3],
                rgb(ANNOUNCEMENT_DARK_COLORS["border_strong"]),
            )
        finally:
            dialog.cleanup()
            UI["workbench_light_theme"] = original_theme
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
