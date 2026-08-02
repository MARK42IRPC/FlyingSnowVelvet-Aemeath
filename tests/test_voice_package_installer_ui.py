import os
import tempfile
import threading
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

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from lib.script.gsvmove.package_manager import (
    VoicePackageRemoteSize,
    VoicePackageStatus,
    get_voice_package_profile,
)
from lib.script.ui.voice_package_installer import (
    VoicePackageInstallBanner,
    VoicePackageInstallerDialog,
    VoicePackageManagementBar,
)
from lib.script.ui import voice_package_installer as installer_ui


class VoicePackageInstallerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_banner_uses_required_call_to_action_and_status(self):
        banner = VoicePackageInstallBanner()
        try:
            banner.set_package_status(VoicePackageStatus("legacy", "old"))
            self.assertTrue(banner.isVisibleTo(banner) or not banner.isHidden())
            self.assertEqual(banner.install_button.text(), "安装最新语音包")
            self.assertIn("旧版 GSVmove", banner._detail.text())

            banner.set_package_status(VoicePackageStatus("installed", "ok", Path("voice")))
            self.assertTrue(banner.isHidden())
        finally:
            banner.deleteLater()

    def test_management_bar_is_available_for_installed_and_invalid_packages(self):
        bar = VoicePackageManagementBar()
        try:
            bar.set_package_status(VoicePackageStatus("installed", "ok", Path("voice")))
            self.assertFalse(bar.isHidden())
            self.assertEqual(bar.remove_button.text(), "删除语音包")
            self.assertIn("VoicePackageRemoveButton", bar.styleSheet())

            bar.set_package_status(VoicePackageStatus("invalid", "broken", Path("broken")))
            self.assertFalse(bar.isHidden())
            self.assertIn("不完整", bar._detail.text())

            bar.set_package_status(VoicePackageStatus("missing", "missing"))
            self.assertTrue(bar.isHidden())
        finally:
            bar.deleteLater()

    def test_management_bar_removes_through_locked_service_api(self):
        class ImmediateHub:
            @staticmethod
            def submit_interactive_io(func):
                func()
                return object()

        package_root = Path("D:/AemeathDeskPet/voice/ONNX_aimisiV2")
        service = Mock()
        service.remove_voice_package.return_value = package_root
        removed = []
        bar = VoicePackageManagementBar()
        bar.package_removed.connect(removed.append)
        try:
            bar.set_package_status(VoicePackageStatus("installed", "ok", package_root))
            with patch.object(bar, "_confirm_removal", return_value=True), patch.object(
                installer_ui, "get_compute_hub", return_value=ImmediateHub()
            ), patch("lib.script.gsvmove.get_gsvmove_service", return_value=service):
                bar._on_remove_clicked()

            service.remove_voice_package.assert_called_once_with(package_root)
            self.assertEqual(removed, [package_root])
            self.assertTrue(bar.isHidden())
            self.assertFalse(bar.is_busy())
        finally:
            bar.deleteLater()

    def test_management_bar_recovers_when_removal_submission_fails(self):
        class FailingHub:
            @staticmethod
            def submit_interactive_io(_func):
                raise RuntimeError("interactive pool unavailable")

        failures = []
        bar = VoicePackageManagementBar()
        bar.removal_failed.connect(failures.append)
        try:
            bar.set_package_status(
                VoicePackageStatus(
                    "installed",
                    "ok",
                    Path("D:/AemeathDeskPet/voice/ONNX_aimisiV2"),
                )
            )
            with patch.object(bar, "_confirm_removal", return_value=True), patch.object(
                installer_ui, "get_compute_hub", return_value=FailingHub()
            ):
                bar._on_remove_clicked()

            self.assertFalse(bar.is_busy())
            self.assertTrue(bar.remove_button.isEnabled())
            self.assertEqual(bar.remove_button.text(), "删除语音包")
            self.assertIn("删除任务启动失败", failures[0])
        finally:
            bar.deleteLater()

    def test_dialog_is_compact_and_has_separate_progress_bars(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            installer_ui, "list_fixed_drive_roots", return_value=(Path(tmp),)
        ):
            dialog = VoicePackageInstallerDialog()
            try:
                dialog._reset()
                self.assertLessEqual(dialog.width(), 520)
                self.assertLessEqual(dialog.height(), 420)
                self.assertIsNot(dialog._download_bar, dialog._extract_bar)
                self.assertEqual(dialog._download_bar.objectName(), "VoiceDownloadProgress")
                self.assertEqual(dialog._extract_bar.objectName(), "VoiceExtractProgress")
                self.assertEqual(dialog._extract_label.text(), "解压、校验与安装进度")
                self.assertEqual(dialog._profile_combo.count(), 3)
                self.assertEqual(dialog._profile_combo.currentData(), "fp16")
                int8_index = dialog._profile_combo.findData("int8")
                self.assertIn("强烈推荐", dialog._profile_combo.itemText(int8_index))
                self.assertEqual(dialog._drive_combo.count(), 1)
                self.assertIn("AemeathDeskPet", dialog._drive_detail.text())
                self.assertIn("安装过程需要", dialog._drive_detail.text())
                profile = get_voice_package_profile("fp16")
                self.assertIn(
                    f"下载包大小为 {installer_ui._format_bytes(profile.archive_bytes)}",
                    dialog._drive_detail.text(),
                )
                self.assertIn(
                    f"安装后占用硬盘空间 {installer_ui._format_bytes(profile.extracted_bytes)}",
                    dialog._drive_detail.text(),
                )
                style = dialog.styleSheet()
                self.assertIn("QComboBox::down-arrow", style)
                self.assertIn("QPushButton#VoiceInstallerPrimary", style)
                self.assertIn("QPushButton#VoiceInstallerBackground", style)
                self.assertIn("color: #000000", style)
                self.assertIn("QAbstractItemView::item:selected", dialog._drive_combo.view().styleSheet())
                dialog._secondary.hide()
                dialog._reset()
                self.assertFalse(dialog._secondary.isHidden())
            finally:
                dialog.cleanup()
                self.app.processEvents()

    def test_drive_popup_is_topmost_and_registered_above_dialog(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            installer_ui, "list_fixed_drive_roots", return_value=(Path(tmp),)
        ):
            dialog = VoicePackageInstallerDialog()
            try:
                dialog._reset()
                dialog.show()
                dialog._drive_combo.showPopup()
                self.app.processEvents()
                popup = dialog._drive_combo.view().window()
                self.assertTrue(popup.windowFlags() & Qt.WindowStaysOnTopHint)
                records = installer_ui.get_layer_manager().snapshot()
                popup_records = [row for row in records if row[3] == "VoicePackageDriveDropdown"]
                self.assertEqual(len(popup_records), 1)
                self.assertGreater(popup_records[0][1], 0)
                dialog._drive_combo.hidePopup()
                records = installer_ui.get_layer_manager().snapshot()
                self.assertFalse(any(row[3] == "VoicePackageDriveDropdown" for row in records))
            finally:
                dialog.cleanup()
                self.app.processEvents()

    def test_remote_sizes_replace_offline_estimates_in_profile_and_drive_details(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            installer_ui, "list_fixed_drive_roots", return_value=(Path(tmp),)
        ):
            dialog = VoicePackageInstallerDialog()
            try:
                dialog._reset()
                remote = VoicePackageRemoteSize(
                    "fp16",
                    2 * 1024 ** 3,
                    "ModelScope",
                    "https://example.test/fp16.rar",
                )
                dialog._apply_remote_sizes(
                    dialog._remote_size_generation,
                    {"fp16": remote},
                )

                index = dialog._profile_combo.findData("fp16")
                self.assertIn("2.0 GiB", dialog._profile_combo.itemText(index))
                self.assertIn("2.0 GiB", dialog._drive_detail.text())
                self.assertIn("ModelScope", dialog._drive_detail.text())
            finally:
                dialog.cleanup()
                self.app.processEvents()

    def test_install_uses_interactive_io_and_enters_busy_progress(self):
        submitted = threading.Event()

        class FakeHub:
            def submit_interactive_io(self, func):
                submitted.set()
                return object()

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            installer_ui, "list_fixed_drive_roots", return_value=(Path(tmp),)
        ), patch.object(installer_ui, "get_compute_hub", return_value=FakeHub()):
            dialog = VoicePackageInstallerDialog()
            try:
                dialog._reset()
                dialog._on_primary()
                self.assertTrue(submitted.is_set())
                self.assertTrue(dialog.is_busy())
                self.assertEqual(dialog._download_bar.minimum(), 0)
                self.assertEqual(dialog._download_bar.maximum(), 0)
                self.assertEqual(dialog._download_bar.format(), "准备中")
                self.assertEqual(dialog._background.text(), "后台安装")
                self.assertFalse(dialog._background.isHidden())
                self.assertTrue(dialog._primary.isHidden())
            finally:
                dialog.cleanup()
                self.app.processEvents()

    def test_install_reprobes_selected_profile_before_offline_space_check(self):
        class ImmediateHub:
            def submit_interactive_io(self, func):
                func()
                return Mock()

        remote = VoicePackageRemoteSize(
            "fp16",
            2 * 1024 ** 3,
            "ModelScope",
            "https://example.test/fp16.rar",
        )
        installer = Mock()
        installer.install.return_value = Mock()
        service = Mock()

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            installer_ui, "list_fixed_drive_roots", return_value=(Path(tmp),)
        ), patch.object(
            installer_ui, "get_compute_hub", return_value=ImmediateHub()
        ), patch.object(
            installer_ui, "fetch_voice_package_size", return_value=remote
        ) as fetch_size, patch.object(
            installer_ui, "VoicePackageInstaller", return_value=installer
        ), patch(
            "lib.script.gsvmove.get_gsvmove_service", return_value=service
        ):
            dialog = VoicePackageInstallerDialog()
            try:
                dialog._reset()
                dialog._on_primary()

                fetch_size.assert_called_once_with("fp16")
                installer.install.assert_called_once()
                self.assertEqual(
                    installer.install.call_args.kwargs["archive_bytes"],
                    remote.archive_bytes,
                )
            finally:
                dialog.cleanup()
                self.app.processEvents()

    def test_background_install_hides_without_cancelling_and_notifies_key_stages(self):
        class RecordingEventCenter:
            def __init__(self):
                self.events = []

            def publish(self, event):
                self.events.append(event)

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            installer_ui, "list_fixed_drive_roots", return_value=(Path(tmp),)
        ), patch.object(installer_ui, "get_event_center", return_value=RecordingEventCenter()) as center:
            dialog = VoicePackageInstallerDialog()
            try:
                installer = Mock()
                dialog._installer = installer
                dialog._busy = True
                dialog.show()

                dialog._move_install_to_background()

                installer.cancel.assert_not_called()
                self.assertTrue(dialog.isHidden())
                self.assertTrue(dialog._backgrounded)
                dialog._apply_progress("download", 10, 100, "正在下载")
                dialog._apply_progress("extract", 0, 0, "正在解压角色模型与公共模型")
                dialog._apply_progress("extract", 1, 100, "正在准备激活新语音包")
                dialog._apply_progress("extract", 2, 100, "正在准备激活新语音包")
                dialog._on_install_cancelled()

                texts = [event.data["text"] for event in center.return_value.events]
                self.assertEqual(
                    texts,
                    [
                        "语音包正在后台安装，关键进度会提醒你。",
                        "语音包下载完成，正在解压与校验。",
                        "语音包校验完成，正在激活。",
                        "ONNX 语音包安装已取消。",
                    ],
                )
            finally:
                dialog.cleanup()
                self.app.processEvents()

    def test_reopening_background_install_restores_progress_without_reset(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            installer_ui, "list_fixed_drive_roots", return_value=(Path(tmp),)
        ):
            dialog = VoicePackageInstallerDialog()
            try:
                dialog._busy = True
                dialog._backgrounded = True
                dialog._status.setText("正在解压语音包")
                dialog._extract_bar.setValue(420)

                dialog.show_dialog()

                self.assertFalse(dialog._backgrounded)
                self.assertTrue(dialog._background.isEnabled())
                self.assertEqual(dialog._status.text(), "正在解压语音包")
                self.assertEqual(dialog._extract_bar.value(), 420)
            finally:
                dialog.cleanup()
                self.app.processEvents()

    def test_install_progress_does_not_move_backward(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            installer_ui, "list_fixed_drive_roots", return_value=(Path(tmp),)
        ):
            dialog = VoicePackageInstallerDialog()
            try:
                dialog._reset()
                dialog._apply_progress("extract", 720, 1000, "正在校验")
                dialog._apply_progress("extract", 650, 1000, "延迟到达的旧进度")

                self.assertEqual(dialog._extract_bar.value(), 720)
                self.assertEqual(dialog._extract_bar.format(), "72.0%")
            finally:
                dialog.cleanup()
                self.app.processEvents()

    def test_install_submission_error_is_shown_in_dialog(self):
        class FailingHub:
            def submit_interactive_io(self, _func):
                raise RuntimeError("interactive pool unavailable")

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            installer_ui, "list_fixed_drive_roots", return_value=(Path(tmp),)
        ), patch.object(installer_ui, "get_compute_hub", return_value=FailingHub()):
            dialog = VoicePackageInstallerDialog()
            try:
                dialog._reset()
                dialog._on_primary()
                self.assertFalse(dialog.is_busy())
                self.assertIn("安装任务启动失败", dialog._status.text())
                self.assertTrue(dialog._primary.isEnabled())
                self.assertEqual(dialog._download_bar.maximum(), 1000)
                self.assertEqual(dialog._download_bar.format(), "安装失败")
            finally:
                dialog.cleanup()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
