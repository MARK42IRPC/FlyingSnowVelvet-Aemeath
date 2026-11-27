"""Voice installer uses the announcement shell and reports worker failures."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch


class VoiceInstallPopupTests(TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PyQt5.QtCore import Qt

        from lib.core.render.backends.qt.widgets import voice_package_installer as host
        from lib.script.ui import voice_package_installer as product

        self.host = host
        self.product = product
        self.qt = Qt
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.drive = Path(tmp.name)
        self.future = Future()
        self.hub = Mock()
        self.hub.submit_interactive_io.return_value = self.future
        self.enterContext(patch.object(product, "list_fixed_drive_roots", return_value=(self.drive,)))
        self.enterContext(patch.object(product, "get_compute_hub", return_value=self.hub))
        self.enterContext(patch.object(product, "fetch_voice_package_size", return_value=None))
        self.enterContext(patch.object(host, "_gsvmove_service"))
        self.installer = Mock()
        self.factory = self.enterContext(patch.object(product, "VoicePackageInstaller", return_value=self.installer))
        self.dialog = product.VoicePackageInstallerDialog()
        self.addCleanup(self._cleanup_dialog)
        self.dialog._reset()
        self.dialog._shell.show_window()
        self.app.processEvents()

    def _cleanup_dialog(self):
        from PyQt5.QtCore import QCoreApplication, QEvent

        self.dialog.cleanup()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def _run_worker(self):
        worker = self.hub.submit_interactive_io.call_args.args[0]
        self.assertTrue(self.future.set_running_or_notify_cancel())
        try:
            result = worker()
        except Exception as exc:
            self.future.set_exception(exc)
        else:
            self.future.set_result(result)

    def _check_notice(self, *, parent, expected):
        notice = self.dialog._failure_notice
        self.assertIsNotNone(notice)
        self.assertTrue(notice.widget.isVisible())
        self.assertIsNone(notice.widget.parentWidget())
        self.assertEqual(notice._anchor_parent, parent)
        self.assertEqual(notice.widget.windowModality(), self.qt.NonModal)
        self.assertEqual(notice.find("header").objectName(), "AnnouncementHeader")
        self.assertEqual(notice.find("header").text(), "语音包安装失败")
        self.assertEqual(notice.find("header_accent_bar").objectName(), "AnnouncementHeaderAccent")
        text = notice.find("body").toPlainText()
        self.assertIn(expected, text)
        self.assertNotIn("private-token", text)
        self.assertNotIn("Secret Drive", text)
        self.assertFalse(self.dialog.is_busy())
        self.assertTrue(self.dialog._primary.isEnabled())
        self.assertEqual(self.dialog._primary.text(), "重新安装")
        self.assertEqual(self.dialog._extract_bar.format(), "安装失败")
        return notice

    def test_shell_and_dropdown_are_shared_and_keep_their_layer(self):
        from lib.core.render.layers import get_layer_manager
        from lib.core.render.visuals.announcement_chrome import announcement_stylesheet

        dialog = self.dialog
        self.assertEqual(dialog.styleSheet(), announcement_stylesheet())
        self.assertTrue(dialog._shell.spec.border_frame)
        self.assertEqual(dialog._shell.find("body").objectName(), "AnnouncementBody")
        self.assertEqual(dialog._title.objectName(), "AnnouncementHeader")
        self.assertEqual(dialog._minimize_btn.objectName(), "AnnouncementMinimizeButton")
        self.assertEqual(dialog._extract_bar.property("windowProgressRole"), "accent")
        self.assertEqual(dialog._background.property("windowActionRole"), "accent")
        self.assertEqual(dialog._primary.property("windowActionRole"), "primary")
        self.assertEqual(dialog._drive_combo.view().styleSheet(), "")
        dialog._drive_combo.showPopup()
        self.assertTrue(dialog._drive_combo.view().window().windowFlags() & self.qt.WindowStaysOnTopHint)
        self.assertTrue(any(row[1] > 0 and row[3] == "VoicePackageDriveDropdown"
                            for row in get_layer_manager().snapshot()))
        dialog._drive_combo.hidePopup()
        dialog._minimize_btn.click()
        self.assertFalse(dialog._shell.is_requested_visible())
        dialog._shell.show_window()
        self.assertTrue(dialog._shell.is_requested_visible())

    def test_post_download_failure_stays_visible_and_is_safe(self):
        from lib.script.gsvmove import package_manager as manager

        def install(*_args, **_kwargs):
            progress = self.factory.call_args.kwargs["progress_callback"]
            progress("download", 3_000_000_000, 3_000_000_000, "下载完成")
            progress("extract", 230, 1000, "正在解压角色模型与公共模型")
            # Exercise the actual post-download failure path without downloading or
            # invoking UnRAR. The low-level exception includes private details.
            error = PermissionError(13, "https://example.test/?private-token", r"C:\Secret Drive\archive.rar")
            with patch.object(manager, "_list_archive_members", side_effect=error):
                manager.VoicePackageInstaller()._extract(Path("UnRAR.exe"), Path("downloaded.rar"), self.drive)

        self.installer.install.side_effect = install
        self.dialog._primary.click()
        self.assertTrue(self.dialog.is_busy())
        self.assertTrue(self.dialog._background.isVisible())
        self.assertFalse(self.dialog._close_btn.isEnabled())
        self.assertFalse(self.dialog.close())
        with patch.object(self.host.logger, "warning") as log:
            self._run_worker()
            self.assertIsNone(self.dialog._failure_notice)
            self.app.processEvents()
            self.assertIn("检查语音包归档失败", str(log.call_args))
            self.assertNotIn("private-token", str(log.call_args))
        notice = self._check_notice(parent=self.dialog, expected="检查语音包归档失败")
        self.assertEqual(self.dialog._download_bar.value(), 1000)
        self.assertEqual(self.dialog._extract_bar.value(), 230)
        notice.find("primary").click()
        self.assertFalse(notice.widget.isVisible())

    def test_background_failure_opens_one_notice_without_reopening_installer(self):
        self.dialog._primary.click()
        self.dialog._background.click()
        self.assertTrue(self.dialog.isHidden())
        self.assertTrue(self.dialog.is_busy())
        self.installer.request_cancel.assert_not_called()
        self.installer.cancel.assert_not_called()
        self.dialog.show_dialog()
        self.assertTrue(self.dialog.isVisible())
        self.assertTrue(self.dialog.is_busy())
        self.dialog._background.click()
        self.future.set_exception(RuntimeError("https://example.test/?private-token"))
        self.app.processEvents()
        notice = self._check_notice(parent=None, expected="安装语音包失败")
        self.assertTrue(self.dialog.isHidden())
        self.dialog._finished_signal.emit(self.dialog._install_generation, "failed", "duplicate")
        self.app.processEvents()
        self.assertIs(self.dialog._failure_notice, notice)

    def test_constructor_and_submission_failures_are_deferred_and_retryable(self):
        for source in (self.factory, self.hub.submit_interactive_io):
            with self.subTest(source=source):
                source.side_effect = RuntimeError("https://example.test/?private-token")
                try:
                    self.dialog._primary.click()
                    self.assertIsNone(self.dialog._failure_notice)
                    self.assertTrue(self.dialog.is_busy())
                    self.app.processEvents()
                    self._check_notice(parent=self.dialog, expected="安装任务启动失败")
                finally:
                    source.side_effect = None
                    self.dialog._close_failure_notice()

    def test_notice_construction_error_leaves_safe_inline_feedback_and_retry(self):
        self.dialog._primary.click()
        self.future.set_exception(PermissionError(13, "private-token"))
        with patch.object(self.host, "post_message", side_effect=RuntimeError("Qt is closing")):
            self.app.processEvents()
        self.assertIsNone(self.dialog._failure_notice)
        self.assertFalse(self.dialog.is_busy())
        self.assertIn("errno 13", self.dialog._status.text())
        self.assertNotIn("private-token", self.dialog._status.text())
        self.assertTrue(self.dialog._primary.isEnabled())

    def test_user_cancel_and_shutdown_keep_late_failures_silent(self):
        self.dialog._primary.click()
        self.assertTrue(self.future.set_running_or_notify_cancel())
        self.dialog._secondary.click()
        self.installer.request_cancel.assert_called_once_with()
        self.installer.cancel.assert_not_called()
        self.hub.submit_io.assert_called_once_with(self.installer.cancel)
        self.future.set_exception(RuntimeError("interrupted extraction"))
        self.app.processEvents()
        self.assertIsNone(self.dialog._failure_notice)
        self.assertEqual(self.dialog._extract_bar.format(), "已取消")

        previous = self.dialog._install_generation
        retry = Future()
        self.hub.submit_interactive_io.return_value = retry
        self.dialog._primary.click()
        self.dialog._finished_signal.emit(previous, "failed", "stale failure")
        self.app.processEvents()
        self.assertTrue(self.dialog.is_busy())
        self.assertIsNone(self.dialog._failure_notice)
        self.assertTrue(retry.set_running_or_notify_cancel())
        retry.set_exception(RuntimeError("late failure during shutdown"))
        self.dialog._on_app_exit(None)
        self.app.processEvents()
        self.assertIsNone(self.dialog._failure_notice)
        self.assertFalse(self.dialog.is_busy())

    def test_cancelling_queued_install_allows_retry_without_notification(self):
        self.dialog._primary.click()
        self.dialog._secondary.click()
        self.assertTrue(self.future.cancelled())
        self.app.processEvents()
        self.assertEqual(self.dialog._extract_bar.format(), "已取消")
        self.assertIsNone(self.dialog._failure_notice)
        self.assertTrue(self.dialog._primary.isEnabled())

    def test_success_delivers_result_and_completes_both_progress_bars(self):
        from lib.script.gsvmove.package_manager import VoiceInstallResult

        result = VoiceInstallResult(self.drive, "Test")
        self.installer.install.return_value = result
        results = []
        self.dialog.install_succeeded.connect(results.append)
        self.dialog._primary.click()
        self._run_worker()
        self.app.processEvents()
        self.assertEqual(results, [result])
        self.assertEqual(self.dialog._download_bar.value(), 1000)
        self.assertEqual(self.dialog._extract_bar.value(), 1000)
        self.assertEqual(self.dialog._primary.text(), "关闭")
        self.assertIsNone(self.dialog._failure_notice)
