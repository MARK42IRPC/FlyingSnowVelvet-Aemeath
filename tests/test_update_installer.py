from __future__ import annotations

import base64
import hashlib
import json
import struct
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.script.app.update_installer import (
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
            info = validate_update_installer(installer)
            self.assertEqual(info.archive_size > 0, True)
            corrupted = bytearray(installer.read_bytes())
            corrupted[8] ^= 0x01
            installer.write_bytes(corrupted)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                validate_update_installer(installer)

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

    def test_corrupt_zip_is_rejected_after_hash_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "corrupt.exe"
            _write_installer(installer, {".fsv-install-root": "marker\n"})
            data = bytearray(installer.read_bytes())
            trailer_offset = len(data) - OFFLINE_INSTALLER_TRAILER_SIZE
            _, archive_size, _ = struct.unpack(
                OFFLINE_INSTALLER_TRAILER_FORMAT, data[trailer_offset:]
            )
            archive_offset = trailer_offset - archive_size
            data[archive_offset] ^= 0xFF
            digest = hashlib.sha256(data[archive_offset:trailer_offset]).digest()
            data[trailer_offset:] = struct.pack(
                OFFLINE_INSTALLER_TRAILER_FORMAT,
                OFFLINE_INSTALLER_MAGIC,
                archive_size,
                digest,
            )
            installer.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "ZIP|损坏"):
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
            release = ReleaseInfo(
                "PACK",
                datetime(2026, 7, 29, tzinfo=timezone.utc),
                "FlyingSnowVelvet-LTS2-Offline-Installer.exe",
                "download",
                "GitHub",
                "revision",
            )

            def download(_release, destination):
                _write_installer(destination, {".fsv-install-root": "marker\n", "app/README.md": "new"})

            with (
                patch("lib.script.update_manager._STAGING_ROOT", root / "stage"),
                patch.object(manager, "_download_release", side_effect=download),
                patch("lib.script.app.update_installer.launch_update_installer") as launch,
            ):
                result = manager.install_release(release)

            self.assertEqual(result.reason, "install_scheduled")
            self.assertFalse(state_path.exists())
            launch.assert_called_once()

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
