from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.script.app.update_installer import (
    build_bat_restart_command,
    install_update_archive,
    launch_update_installer,
    run_update_installer,
    validate_update_archive,
)
from lib.core.event.center import EventType
from lib.script.update_manager import (
    InstalledState,
    ReleaseInfo,
    UpdateManager,
    UpdateResult,
)


def _write_archive(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)


class UpdateInstallerTests(unittest.TestCase):
    def test_bat_restart_command_selects_normal_and_environment_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            normal = root / "启动程序.bat"
            environment = root / "安装依赖.bat"
            normal.write_text("@echo off\n", encoding="utf-8")
            environment.write_text("@echo off\n", encoding="utf-8")

            normal_command = build_bat_restart_command(root, "normal")
            environment_command = build_bat_restart_command(root, "environment")

            self.assertIn("/c", normal_command)
            self.assertEqual(normal_command[-1], str(normal.resolve()))
            self.assertEqual(environment_command[-1], str(environment.resolve()))

    def test_install_overwrites_application_but_preserves_user_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            (project / "app.py").write_text("old", encoding="utf-8")
            protected = {
                "resc/user/settings.json": "user",
                "resc/models/model.bin": "model",
                "logs/current.log": "log",
                "py.ini": "python",
            }
            for relative, content in protected.items():
                target = project / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            archive = root / "update.zip"
            entries = {"package/app.py": "new", "package/README.md": "readme"}
            entries.update({f"package/{name}": "replaced" for name in protected})
            _write_archive(archive, entries)
            state_path = project / "resc/user/update_state.json"

            count = install_update_archive(
                archive,
                project,
                state_path,
                {
                    "tag": "PACK",
                    "published_at": "2026-07-29T00:00:00Z",
                    "revision": "abc",
                    "source": "GitHub",
                },
            )

            self.assertGreater(count, 0)
            self.assertEqual((project / "app.py").read_text(encoding="utf-8"), "new")
            for relative, content in protected.items():
                self.assertEqual((project / relative).read_text(encoding="utf-8"), content)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["revision"], "abc")

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "unsafe.zip"
            _write_archive(archive, {"../outside.txt": "bad"})
            with self.assertRaisesRegex(ValueError, "不安全路径"):
                validate_update_archive(archive)

    @patch("lib.script.app.update_installer._write_update_log")
    @patch("lib.script.app.update_installer._is_process_running", return_value=True)
    @patch("lib.script.app.update_installer.install_update_archive")
    @patch("lib.script.app.update_installer.subprocess.Popen")
    def test_parent_timeout_neither_installs_nor_restarts(
        self, popen, install, _running, _log
    ):
        payload = {
            "project_root": "C:/app",
            "archive_path": "C:/temp/update.zip",
            "state_path": "C:/app/resc/user/update_state.json",
            "parent_pid": 123,
            "restart_command": ["python", "app.py"],
            "release": {},
        }
        self.assertEqual(run_update_installer(payload, max_wait=0), 2)
        install.assert_not_called()
        popen.assert_not_called()

    @patch("lib.script.app.update_installer.shutil.rmtree")
    @patch("lib.script.app.update_installer._write_update_log")
    @patch("lib.script.app.update_installer._is_process_running", return_value=False)
    @patch("lib.script.app.update_installer.install_update_archive", return_value=10)
    @patch("lib.script.app.update_installer.subprocess.Popen")
    def test_successful_install_restarts_new_application(
        self, popen, install, _running, _log, _rmtree
    ):
        popen.return_value.pid = 456
        payload = {
            "project_root": "C:/app",
            "archive_path": "C:/temp/update.zip",
            "state_path": "C:/app/resc/user/update_state.json",
            "parent_pid": 123,
            "restart_command": ["python", "app.py"],
            "release": {"tag": "PACK"},
        }
        self.assertEqual(run_update_installer(payload), 0)
        install.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["python", "app.py"])

    @patch("lib.script.app.update_installer.subprocess.Popen")
    @patch("lib.script.app.update_installer.build_restart_command", return_value=["python", "app.py"])
    def test_launch_uses_dedicated_update_helper(self, _command, popen):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "stage/update.zip"
            archive.parent.mkdir()
            archive.write_bytes(b"zip")
            launch_update_installer(
                archive,
                root,
                root / "state.json",
                {"tag": "PACK"},
            )

        command = popen.call_args.args[0]
        self.assertIn("--fsv-update-helper", command)
        payload = json.loads(command[-1])
        self.assertEqual(payload["parent_pid"] > 0, True)
        self.assertEqual(payload["restart_command"], ["python", "app.py"])

    def test_manager_only_downloads_and_hands_off_before_exit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "state.json"
            manager = UpdateManager(state_path=state_path)
            release = ReleaseInfo(
                "PACK",
                datetime(2026, 7, 29, tzinfo=timezone.utc),
                "pack.zip",
                "download",
                "GitHub",
                "revision",
            )

            def download(_release, destination):
                _write_archive(destination, {"package/README.md": "new"})

            with (
                patch("lib.script.update_manager._STAGING_ROOT", root / "stage"),
                patch.object(manager, "_download_release", side_effect=download),
                patch("lib.script.app.update_installer.launch_update_installer") as launch,
            ):
                result = manager.install_release(release)

            self.assertEqual(result.reason, "install_scheduled")
            self.assertFalse(state_path.exists())
            launch.assert_called_once()

    def test_dialog_offers_restart_modes_after_download(self):
        from lib.script.ui.update_dialog import DesktopPetUpdateDialog

        published = datetime(2026, 7, 29, tzinfo=timezone.utc)
        release = ReleaseInfo("PACK", published, "pack.zip", "download", "GitHub")
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
            _start_normal_restart=Mock(),
            _start_environment_restart=Mock(),
        )
        center = Mock()
        with patch("lib.script.ui.update_dialog.get_event_center", return_value=center):
            DesktopPetUpdateDialog._on_release_done(dialog, result)

        center.publish.assert_not_called()
        self.assertEqual(dialog._status_label.setText.call_args.args[0], "更新依赖并重启桌宠")
        actions = dialog._set_actions.call_args.args
        self.assertEqual(actions[0], ("普通重启", dialog._start_normal_restart))
        self.assertEqual(actions[1], ("环境重启", dialog._start_environment_restart))


if __name__ == "__main__":
    unittest.main()
