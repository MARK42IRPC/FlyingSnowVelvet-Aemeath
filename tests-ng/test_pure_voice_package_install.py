"""Pure regression tests for transactional ONNX voice-package activation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.gsvmove import package_manager as manager


class VoicePackageActivationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "incoming"
        self.target = self.root / manager.VOICE_PACKAGE_DIR_NAME
        self.source.mkdir()
        self.target.mkdir()
        (self.source / "new-model.onnx").write_bytes(b"new")
        (self.target / "old-model.onnx").write_bytes(b"old")

    def test_failed_old_package_move_does_not_delete_existing_package(self):
        original_replace = Path.replace

        def replace(path: Path, destination: Path):
            if path == self.target:
                raise PermissionError("simulated locked package")
            return original_replace(path, destination)

        with (
            patch.object(Path, "replace", autospec=True, side_effect=replace),
            patch.object(manager, "_write_install_state") as write_state,
        ):
            with self.assertRaisesRegex(PermissionError, "simulated locked package"):
                manager.VoicePackageInstaller._activate_package(self.source, self.target, {})

        write_state.assert_not_called()
        self.assertEqual((self.target / "old-model.onnx").read_bytes(), b"old")
        self.assertEqual((self.source / "new-model.onnx").read_bytes(), b"new")
        self.assertEqual(set(self.root.iterdir()), {self.source, self.target})

    def test_archive_audit_failure_is_a_labeled_stage_and_stops_extraction(self):
        installer = manager.VoicePackageInstaller()
        info_messages = []
        installer._info_callback = info_messages.append
        with patch.object(
            manager,
            "_list_archive_members",
            side_effect=manager.VoicePackageError("模拟归档损坏 https://example.test/?private-token"),
        ), patch.object(manager.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(
                manager.VoicePackageError,
                "^检查语音包归档失败：安装过程中出现异常$",
            ) as raised:
                installer._extract(
                    Path("UnRAR.exe"),
                    Path("downloaded.rar"),
                    Path("extract"),
                )

        self.assertIsInstance(raised.exception.__cause__, manager.VoicePackageError)
        self.assertEqual(info_messages, ["下载完成，正在检查语音包归档"])
        popen.assert_not_called()

    def test_public_message_keeps_error_code_not_exception_details(self):
        error = PermissionError(13, "https://example.test/?secret", r"C:\Secret Drive\private.rar")
        self.assertEqual(
            manager.voice_package_error_message(error, stage="解压语音包"),
            "解压语音包失败：权限不足或文件被占用（errno 13）",
        )
        self.assertNotIn("private", manager.voice_package_error_message(manager.VoicePackageError("/tmp/private")))

    def test_request_cancel_does_not_call_process_or_session_cleanup(self):
        installer = manager.VoicePackageInstaller()
        with (
            patch.object(manager, "_terminate_process") as terminate,
            patch.object(installer, "_close_download_sessions") as close_sessions,
        ):
            installer.request_cancel()
            terminate.assert_not_called()
            close_sessions.assert_not_called()
        with self.assertRaises(manager.VoicePackageCancelled):
            installer._check_cancelled()

    def test_failed_state_write_restores_old_package(self):
        with patch.object(
            manager, "_write_install_state", side_effect=OSError("simulated state write failure")
        ) as write_state:
            with self.assertRaisesRegex(OSError, "simulated state write failure"):
                manager.VoicePackageInstaller._activate_package(self.source, self.target, {})

        write_state.assert_called_once_with(self.target, {})
        self.assertEqual((self.target / "old-model.onnx").read_bytes(), b"old")
        self.assertFalse((self.target / "new-model.onnx").exists())
        self.assertEqual(set(self.root.iterdir()), {self.target})

    def test_extract_retries_with_staged_unrar_copy_after_create_error_9(self):
        installer = manager.VoicePackageInstaller()
        unrar_src = self.root / "bundled" / "UnRAR.exe"
        unrar_src.parent.mkdir()
        unrar_src.write_bytes(b"fake-unrar-binary")
        stage_root = self.root / "stage"
        extract_root = stage_root / "extract"
        extract_root.mkdir(parents=True)
        archive = stage_root / "voice.rar"
        archive.write_bytes(b"Rar!")

        invoked_paths: list[Path] = []

        class _FakeProc:
            def __init__(self, returncode: int):
                self.returncode = returncode

            def poll(self):
                return self.returncode

        def fake_popen(command, **_kwargs):
            executable = Path(command[0])
            invoked_paths.append(executable)
            return _FakeProc(9 if len(invoked_paths) == 1 else 0)

        with (
            patch.object(manager, "_list_archive_members", return_value=("manifest.json",)),
            patch.object(manager.subprocess, "Popen", side_effect=fake_popen),
            patch.object(manager.os, "name", "nt"),
        ):
            installer._extract(unrar_src, archive, extract_root)

        staged = stage_root / "UnRAR.exe"
        self.assertEqual(invoked_paths, [unrar_src, staged])
        self.assertEqual(staged.read_bytes(), b"fake-unrar-binary")

    def test_extract_reports_actionable_message_when_error_9_persists(self):
        installer = manager.VoicePackageInstaller()
        unrar_src = self.root / "UnRAR.exe"
        unrar_src.write_bytes(b"fake-unrar")
        stage_root = self.root / "stage"
        extract_root = stage_root / "extract"
        extract_root.mkdir(parents=True)
        archive = stage_root / "voice.rar"
        archive.write_bytes(b"Rar!")

        class _FailProc:
            returncode = 9

            def poll(self):
                return 9

        with (
            patch.object(manager, "_list_archive_members", return_value=("manifest.json",)),
            patch.object(manager.subprocess, "Popen", return_value=_FailProc()),
            patch.object(manager.os, "name", "nt"),
        ):
            with self.assertRaisesRegex(manager.VoicePackageError, "错误代码 9.*写入权限"):
                installer._extract(unrar_src, archive, extract_root)


if __name__ == "__main__":
    unittest.main()
