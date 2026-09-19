from __future__ import annotations

import base64
import hashlib
import struct
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.script.app import update_installer
from lib.script.app.update_locks import LockReleaseReport
from lib.script.app.update_installer import (
    OverlayOutcome,
    clear_update_installer_cache,
    extract_update_installer_bundle,
    launch_update_installer,
    validate_update_installer,
    OFFLINE_INSTALLER_MAGIC,
    OFFLINE_INSTALLER_TRAILER_FORMAT,
    OFFLINE_INSTALLER_TRAILER_SIZE,
)
from lib.core.event.center import EventType
from lib.script.app.windows_command import build_bat_command
from lib.script.update_manager import (
    InstalledState,
    ReleaseInfo,
    UpdateError,
    UpdateManager,
    UpdateResult,
)


def _write_installer(path: Path, entries: dict[str, str]) -> None:
    buffer = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        with zipfile.ZipFile(buffer, "w") as bundle:
            for name, content in entries.items():
                bundle.writestr(name, content)
        buffer.seek(0)
        archive = buffer.read()
    finally:
        buffer.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"MZ" + b"stub" + archive + struct.pack(
            OFFLINE_INSTALLER_TRAILER_FORMAT,
            OFFLINE_INSTALLER_MAGIC,
            len(archive),
            hashlib.sha256(archive).digest(),
        )
    )


def _rewrite_trailer(path: Path, *, archive_size=None, digest=None, magic=None) -> None:
    data = bytearray(path.read_bytes())
    trailer_offset = len(data) - OFFLINE_INSTALLER_TRAILER_SIZE
    old_magic, old_size, old_digest = struct.unpack(
        OFFLINE_INSTALLER_TRAILER_FORMAT, data[trailer_offset:]
    )
    data[trailer_offset:] = struct.pack(
        OFFLINE_INSTALLER_TRAILER_FORMAT,
        old_magic if magic is None else magic,
        old_size if archive_size is None else archive_size,
        old_digest if digest is None else digest,
    )
    path.write_bytes(data)


class UpdateInstallerTests(unittest.TestCase):
    def test_bat_restart_command_selects_normal_and_environment_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            normal = root / "启动程序.bat"
            environment = root / "安装依赖.bat"
            normal.write_text("@echo off\n", encoding="utf-8")
            environment.write_text("@echo off\n", encoding="utf-8")

            normal_command = build_bat_command(root, "normal")
            environment_command = build_bat_command(root, "environment")

            self.assertIn("-EncodedCommand", normal_command)
            normal_script = base64.b64decode(normal_command[-1]).decode("utf-16-le")
            environment_script = base64.b64decode(environment_command[-1]).decode("utf-16-le")
            self.assertIn("FromBase64String", normal_script)
            self.assertIn("FromBase64String", environment_script)

    def test_offline_installer_trailer_and_payload_are_validated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "FlyingSnowVelvet-LTS2-Offline-Installer.exe"
            _write_installer(installer, {".fsv-install-root": "marker\n", "app/readme.txt": "ok"})
            info = validate_update_installer(installer, verify_payload=True)
            self.assertEqual(info.archive_size > 0, True)
            corrupted = bytearray(installer.read_bytes())
            corrupted[8] ^= 0x01
            installer.write_bytes(corrupted)
            clear_update_installer_cache()
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                validate_update_installer(installer, verify_payload=True)

    def test_default_validation_never_reads_the_payload(self):
        """默认校验只认发布清单的哈希：不重算内置归档，也不逐条 CRC。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "FlyingSnowVelvet-LTS2-Offline-Installer.exe"
            _write_installer(
                installer, {".fsv-install-root": "marker\n", "app/readme.txt": "ok"}
            )
            with (
                patch.object(
                    update_installer,
                    "_hash_file_range",
                    side_effect=AssertionError("不应重新哈希内置归档"),
                ),
                patch.object(
                    update_installer.zipfile.ZipFile,
                    "testzip",
                    side_effect=AssertionError("不应逐条校验 payload"),
                ),
            ):
                info = validate_update_installer(installer)
            self.assertEqual(info.archive_size > 0, True)

    def test_validation_result_is_cached_per_file_state(self):
        """同一次更新里安装器会被校验两遍，第二遍必须直接命中缓存。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "FlyingSnowVelvet-LTS2-Offline-Installer.exe"
            _write_installer(installer, {".fsv-install-root": "marker\n"})
            real_reader = update_installer._read_offline_installer_trailer
            calls: list[Path] = []

            def counting_reader(path):
                calls.append(Path(path))
                return real_reader(path)

            with patch.object(
                update_installer, "_read_offline_installer_trailer", counting_reader
            ):
                first = validate_update_installer(installer)
                second = validate_update_installer(installer)
            self.assertEqual(len(calls), 1)
            self.assertEqual(first, second)

    def test_truncated_installer_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "truncated.exe"
            _write_installer(installer, {".fsv-install-root": "marker\n"})
            installer.write_bytes(installer.read_bytes()[:-1])
            with self.assertRaises(ValueError):
                validate_update_installer(installer)

    def test_invalid_magic_and_archive_size_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for suffix, mutate in (
                ("magic", lambda path: _rewrite_trailer(path, magic=b"bad")),
                ("size", lambda path: _rewrite_trailer(path, archive_size=10**9)),
            ):
                installer = root / f"{suffix}.exe"
                _write_installer(installer, {".fsv-install-root": "marker\n"})
                mutate(installer)
                with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                    validate_update_installer(installer)

    def test_broken_zip_directory_is_rejected_without_reading_the_payload(self):
        """内置归档的 ZIP 目录必须先能读出来，路径也必须安全。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "corrupt.exe"
            _write_installer(installer, {".fsv-install-root": "marker\n"})
            data = bytearray(installer.read_bytes())
            trailer_offset = len(data) - OFFLINE_INSTALLER_TRAILER_SIZE
            _, archive_size, _ = struct.unpack(
                OFFLINE_INSTALLER_TRAILER_FORMAT, data[trailer_offset:]
            )
            # 打坏中央目录尾记录：不再逐条解压也要能立刻拒绝。
            data[trailer_offset - 22] ^= 0xFF
            installer.write_bytes(data)
            clear_update_installer_cache()
            with self.assertRaisesRegex(ValueError, "ZIP"):
                validate_update_installer(installer)

    def test_unsafe_member_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "unsafe.exe"
            _write_installer(
                installer, {".fsv-install-root": "marker\n", "../escape.txt": "nope"}
            )
            with self.assertRaisesRegex(ValueError, "不安全路径"):
                validate_update_installer(installer)

    def test_outer_bundle_extracts_only_one_native_installer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            installer = root / "FlyingSnowVelvet-LTS2-Offline-Installer.exe"
            bundle = root / "FlyingSnowVelvet-LTS2-Offline-Installer.zip"
            extracted_root = root / "extracted"
            _write_installer(installer, {".fsv-install-root": "marker\n"})
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.write(installer, installer.name)

            extracted = extract_update_installer_bundle(bundle, extracted_root)

            self.assertEqual(extracted.name, installer.name)
            self.assertEqual(extracted.read_bytes(), installer.read_bytes())
            validate_update_installer(extracted)

    def test_outer_bundle_rejects_extra_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "installer.zip"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("installer.exe", b"MZ")
                archive.writestr("readme.txt", b"not allowed")

            with self.assertRaisesRegex(ValueError, "只包含一个文件"):
                extract_update_installer_bundle(bundle, root / "extracted")

    @patch("lib.script.app.update_installer.subprocess.Popen")
    def test_launch_uses_native_installer_and_pending_state(self, popen):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            installer = root / "stage/FlyingSnowVelvet-LTS2-Offline-Installer.exe"
            installer.parent.mkdir()
            _write_installer(installer, {".fsv-install-root": "marker\n"})
            launch_update_installer(installer, root, root / "state.json", {"tag": "PACK"})

        command = popen.call_args.args[0]
        self.assertEqual(command[0], str(installer.resolve()))
        self.assertIn("--update-target", command)
        self.assertTrue(any(str(item).endswith(".json") for item in command))

    def test_manager_only_downloads_and_hands_off_before_exit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "state.json"
            manager = UpdateManager(state_path=state_path)
            digest = "b" * 64
            release = ReleaseInfo(
                "PACK",
                datetime(2026, 7, 29, tzinfo=timezone.utc),
                "FlyingSnowVelvet-LTS2-Offline-Installer.exe",
                "download",
                "GitHub",
                "revision",
                0.0,
                (),
                digest,
            )

            def download(_release, destination):
                _write_installer(destination, {".fsv-install-root": "marker\n", "app/README.md": "new"})
                return digest

            with (
                patch("lib.script.update_manager._STAGING_ROOT", root / "stage"),
                patch.object(manager, "_download_release", side_effect=download),
                patch("lib.script.app.update_installer.launch_update_installer") as launch,
                patch(
                    "lib.script.app.update_locks.release_install_directory_locks",
                    return_value=LockReleaseReport(),
                ),
            ):
                result = manager.install_release(release)

            self.assertEqual(result.reason, "install_scheduled")
            self.assertFalse(state_path.exists())
            launch.assert_called_once()

    def test_install_release_accepts_the_streamed_digest_and_rejects_a_mismatch(self):
        """清单哈希与下载流算出的 SHA-256 一致才放行，且不再重读下载文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = b"resource payload" * 32
            digest = hashlib.sha256(payload).hexdigest()
            published = datetime(2026, 9, 15, tzinfo=timezone.utc)
            release = ReleaseInfo(
                "PACK",
                published,
                "FlyingSnowVelvet-PACK-Resources.zip",
                "download",
                "HF",
                "rev",
                0.1,
                (),
                digest,
                "resources",
            )
            manager = UpdateManager(state_path=root / "state.json")

            def download(_release, destination, expected=digest):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
                return expected

            with (
                patch("lib.script.update_manager._STAGING_ROOT", root / "stage"),
                patch.object(manager, "_download_release", side_effect=download),
                patch(
                    "lib.script.app.update_installer.install_resource_bundle",
                    return_value=OverlayOutcome(target_root=root),
                ),
                patch(
                    "lib.script.app.update_locks.release_install_directory_locks",
                    return_value=LockReleaseReport(),
                ) as locks,
                patch.object(
                    update_installer,
                    "_hash_file_range",
                    side_effect=AssertionError("不应重读已下载的更新包"),
                ),
            ):
                result = manager.install_release(release)

            self.assertEqual(result.reason, "resources_installed")
            self.assertEqual(result.notes, ())
            locks.assert_called_once()

            def mismatched(_release, destination):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"tampered")
                return hashlib.sha256(b"tampered").hexdigest()

            with (
                patch("lib.script.update_manager._STAGING_ROOT", root / "stage2"),
                patch.object(manager, "_download_release", side_effect=mismatched),
                patch(
                    "lib.script.app.update_installer.install_resource_bundle",
                    return_value=OverlayOutcome(target_root=root),
                ),
                patch(
                    "lib.script.app.update_locks.release_install_directory_locks",
                    return_value=LockReleaseReport(),
                ),
            ):
                with self.assertRaisesRegex(UpdateError, "SHA-256"):
                    manager.install_release(release)

    def test_manifest_without_sha256_is_refused_instead_of_downgraded(self):
        """缺 sha256 的清单必须直接拒绝安装，不能退化成“自己验自己”。

        旧行为会在这里回退到 `verify_payload=True`：期望值来自尾部记录、被校验的
        内容也来自同一份下载文件，等于自己验自己，挡不住篡改。
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = UpdateManager(state_path=root / "state.json")
            release = ReleaseInfo(
                "PACK",
                datetime(2026, 7, 29, tzinfo=timezone.utc),
                "FlyingSnowVelvet-LTS2-Offline-Installer.exe",
                "download",
                "GitHub",
                "revision",
            )

            def download(_release, destination):
                _write_installer(destination, {".fsv-install-root": "marker\n"})
                return None

            with (
                patch("lib.script.update_manager._STAGING_ROOT", root / "stage"),
                patch.object(manager, "_download_release", side_effect=download),
                patch.object(
                    update_installer,
                    "_hash_file_range",
                    side_effect=AssertionError("缺哈希时不应回退到重哈希内置归档"),
                ),
                patch("lib.script.app.update_installer.launch_update_installer") as launch,
            ):
                with self.assertRaisesRegex(UpdateError, "缺少 SHA-256"):
                    manager.install_release(release)

            launch.assert_not_called()

    def test_manager_defers_locked_resource_files_and_reports_them(self):
        """被占用而没能替换的文件不让整次更新失败，而是登记为下次启动补装。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "install"
            install_root.mkdir()
            payload = b"resource payload"
            digest = hashlib.sha256(payload).hexdigest()
            published = datetime(2026, 9, 15, tzinfo=timezone.utc)
            release = ReleaseInfo(
                "PACK",
                published,
                "FlyingSnowVelvet-PACK-Resources.zip",
                "download",
                "HF",
                "rev",
                0.1,
                (),
                digest,
                "resources",
            )
            manager = UpdateManager(state_path=root / "state.json")
            staging = root / "stage" / "uuid" / ".fsv-resource-1"

            def download(_release, destination):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
                return digest

            with (
                patch("lib.script.update_manager._STAGING_ROOT", root / "stage"),
                patch.object(manager, "_download_release", side_effect=download),
                patch(
                    "lib.script.app.update_installer.install_resource_bundle",
                    return_value=OverlayOutcome(
                        locked=("app/onnx.dll",), staging=staging, target_root=install_root
                    ),
                ),
                patch(
                    "lib.script.app.update_installer.defer_overlay_leftovers",
                    return_value=("app/onnx.dll",),
                ) as defer,
                patch(
                    "lib.script.app.update_locks.release_install_directory_locks",
                    return_value=LockReleaseReport(
                        processes=("pythonw.exe(99)",), workbench_helper_pid=99
                    ),
                ),
                patch("lib.script.update_manager._restart_workbench_window") as restart,
            ):
                result = manager.install_release(release)

            self.assertEqual(result.reason, "resources_installed")
            joined = "\n".join(result.notes)
            self.assertIn("app/onnx.dll", joined)
            self.assertIn("1 个占用安装目录的后台进程", joined)
            self.assertEqual(defer.call_args.args[1], install_root)
            # 覆盖安装做完才重开控制面板，免得它重新锁住刚腾出来的文件。
            restart.assert_called_once()

    def test_installer_handoff_never_reopens_the_workbench_window(self):
        """桌宠马上要退出：任何还活着的自有进程都会挡住原生安装器换目录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = UpdateManager(state_path=root / "state.json")
            digest = "c" * 64
            release = ReleaseInfo(
                "PACK",
                datetime(2026, 9, 15, tzinfo=timezone.utc),
                "FlyingSnowVelvet-PACK-Offline-Installer.exe",
                "download",
                "HF",
                "rev",
                0.0,
                (),
                digest,
            )

            def download(_release, destination):
                _write_installer(
                    destination, {".fsv-install-root": "marker\n"}
                )
                return digest

            with (
                patch("lib.script.update_manager._STAGING_ROOT", root / "stage"),
                patch.object(manager, "_download_release", side_effect=download),
                patch(
                    "lib.script.app.update_locks.release_install_directory_locks",
                    return_value=LockReleaseReport(
                        processes=("pythonw.exe(99)",), workbench_helper_pid=99
                    ),
                ),
                patch("lib.script.app.update_installer.launch_update_installer") as launch,
                patch("lib.script.update_manager._restart_workbench_window") as restart,
            ):
                result = manager.install_release(release)

            launch.assert_called_once()
            self.assertEqual(result.reason, "install_scheduled")
            self.assertIn("1 个占用安装目录的后台进程", result.notes[0])
            restart.assert_not_called()

    def test_dialog_offers_native_installer_after_download(self):
        from lib.script.ui.update_dialog import DesktopPetUpdateDialog

        published = datetime(2026, 7, 29, tzinfo=timezone.utc)
        release = ReleaseInfo("PACK", published, "FlyingSnowVelvet-PACK-Offline-Installer.exe", "download", "GitHub")
        result = UpdateResult(
            True,
            InstalledState("PACK", published),
            release,
            reason="install_scheduled",
        )
        dialog = SimpleNamespace(
            _status_label=Mock(),
            _detail_label=Mock(),
            _set_busy=Mock(),
            _set_progress_done=Mock(),
            _set_actions=Mock(),
            _fmt_dt=lambda value: value.isoformat(),
            _start_release_launch=Mock(),
            hide_dialog=Mock(),
        )
        center = Mock()
        with patch("lib.script.ui.update_dialog.get_event_center", return_value=center):
            DesktopPetUpdateDialog._on_release_done(dialog, result)

        center.publish.assert_not_called()
        self.assertEqual(dialog._status_label.setText.call_args.args[0], "离线安装器已准备")
        actions = dialog._set_actions.call_args.args
        self.assertEqual(actions[0][0], "稍后安装")
        self.assertEqual(actions[1], ("启动安装器并退出", dialog._start_release_launch))

    def test_dialog_finishes_resource_overlay_without_launching_exe(self):
        from lib.script.ui.update_dialog import DesktopPetUpdateDialog

        published = datetime(2026, 9, 7, tzinfo=timezone.utc)
        result = UpdateResult(
            True,
            InstalledState("LTS1.0.7pre2", published, "rev"),
            ReleaseInfo(
                "LTS1.0.7pre2", published, "FlyingSnowVelvet-LTS1.0.7pre2-Resources.zip",
                "download", "ModelScope", "rev", kind="resources"
            ),
            reason="resources_installed",
        )
        dialog = SimpleNamespace(
            _status_label=Mock(),
            _detail_label=Mock(),
            _set_busy=Mock(),
            _set_progress_done=Mock(),
            _set_actions=Mock(),
            _fmt_dt=lambda value: value.isoformat(),
            hide_dialog=Mock(),
        )
        DesktopPetUpdateDialog._on_release_done(dialog, result)

        self.assertEqual(dialog._status_label.setText.call_args.args[0], "资源包已安装")
        dialog._set_actions.assert_called_once_with(None, ("关闭", dialog.hide_dialog))

    def test_native_installer_launch_requests_app_quit(self):
        from lib.script.ui.update_dialog import DesktopPetUpdateDialog

        published = datetime(2026, 7, 29, tzinfo=timezone.utc)
        result = UpdateResult(
            True,
            InstalledState("PACK", published),
            ReleaseInfo("PACK", published, "installer.exe", "download", "GitHub"),
            reason="install_scheduled",
        )
        dialog = SimpleNamespace(_busy=False, _pending_update=result, _set_busy=Mock(), _set_actions=Mock())
        center = Mock()
        with patch("lib.script.ui.update_dialog.get_event_center", return_value=center):
            DesktopPetUpdateDialog._on_restart_done(dialog, result)

        center.publish.assert_called_once()
        event = center.publish.call_args.args[0]
        self.assertEqual(event.type, EventType.APP_QUIT)
        self.assertEqual(event.data, {"exit_code": 0})


if __name__ == "__main__":
    unittest.main()
