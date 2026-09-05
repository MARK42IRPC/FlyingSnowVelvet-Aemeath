import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication

from lib.core.cuda_runtime_installer import CudaRuntimeInstallResult
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


if __name__ == "__main__":
    unittest.main()
