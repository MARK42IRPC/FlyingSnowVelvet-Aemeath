from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.app.desktop_shortcut import (
    _create_shortcut_via_powershell,
    _get_shortcut_target_via_powershell,
)
from lib.script.app.update_installer import build_bat_restart_command


ROOT = Path(__file__).resolve().parents[1]
SPECIAL_SEGMENT = "profile & bang! 100% O'Brien"


class WindowsSpecialCharacterLaunchTests(unittest.TestCase):
    def test_shortcut_values_are_transferred_outside_powershell_argv(self):
        root = rf"C:\Users\{SPECIAL_SEGMENT}\Flying Snow"
        shortcut_path = rf"{root}\Desktop\飞行雪绒.lnk"
        target_path = rf"{root}\启动程序.bat"
        icon_path = rf"{root}\resc\icon.ico"

        with (
            patch(
                "lib.script.app.desktop_shortcut._run_capture_text",
                return_value=(0, "", ""),
            ) as run,
            patch("lib.script.app.desktop_shortcut.os.path.exists", return_value=True),
        ):
            ok, message = _create_shortcut_via_powershell(
                shortcut_path,
                target_path,
                root,
                "飞行雪绒桌面宠物",
                icon_path,
            )

        self.assertTrue(ok, message)
        command = run.call_args.args[0]
        command_text = "\0".join(command)
        self.assertIn("-EncodedCommand", command)
        self.assertNotIn(root, command_text)
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["FSV_SHORTCUT_PATH"], shortcut_path)
        self.assertEqual(environment["FSV_SHORTCUT_TARGET"], target_path)
        self.assertEqual(environment["FSV_SHORTCUT_WORKING_DIR"], root)
        self.assertEqual(environment["FSV_SHORTCUT_ICON"], icon_path)

    def test_shortcut_read_path_is_transferred_outside_powershell_argv(self):
        shortcut_path = rf"C:\Users\{SPECIAL_SEGMENT}\Desktop\飞行雪绒.lnk"
        target_path = rf"C:\Users\{SPECIAL_SEGMENT}\Flying Snow\启动程序.bat"

        with patch(
            "lib.script.app.desktop_shortcut._run_capture_text",
            return_value=(0, target_path + "\r\n", ""),
        ) as run:
            target, message = _get_shortcut_target_via_powershell(shortcut_path)

        self.assertEqual(message, "")
        self.assertEqual(target, target_path)
        self.assertNotIn(shortcut_path, "\0".join(run.call_args.args[0]))
        self.assertEqual(
            run.call_args.kwargs["env"]["FSV_SHORTCUT_PATH"],
            shortcut_path,
        )

    def test_batch_entries_do_not_enable_delayed_expansion(self):
        for name in ("启动程序.bat", "调试模式.bat", "安装依赖.bat"):
            content = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("EnableDelayedExpansion", content, name)
            self.assertIn("DisableDelayedExpansion", content, name)

        for name in ("启动程序.bat", "调试模式.bat"):
            content = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("windows_launcher.ps1", content, name)
            self.assertIn('cd /d "%~dp0"', content, name)
            self.assertNotIn("%~sdp0", content, name)

    @unittest.skipUnless(os.name == "nt", "Windows integration test")
    def test_powershell_shortcut_round_trip_with_special_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / SPECIAL_SEGMENT
            desktop = root / "Desktop"
            desktop.mkdir(parents=True)
            target_path = root / "启动程序.bat"
            target_path.write_text("@echo off\r\n", encoding="utf-8")
            shortcut_path = desktop / "飞行雪绒.lnk"

            ok, message = _create_shortcut_via_powershell(
                str(shortcut_path),
                str(target_path),
                str(root),
                "飞行雪绒桌面宠物",
                "",
            )
            self.assertTrue(ok, message)
            target, message = _get_shortcut_target_via_powershell(str(shortcut_path))
            self.assertEqual(message, "")
            self.assertEqual(os.path.normcase(target or ""), os.path.normcase(str(target_path)))

    @unittest.skipUnless(os.name == "nt", "Windows integration test")
    def test_update_restart_runs_batch_from_special_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / SPECIAL_SEGMENT
            root.mkdir()
            marker = root / "started.txt"
            batch_path = root / "启动程序.bat"
            batch_path.write_text(
                '@echo off\r\nsetlocal DisableDelayedExpansion\r\n'
                '> "%~dp0started.txt" echo started\r\n',
                encoding="utf-8",
            )

            command = build_bat_restart_command(root, "normal")
            self.assertNotIn(str(root), "\0".join(command))
            result = subprocess.run(
                command,
                capture_output=True,
                text=False,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows integration test")
    def test_normal_batch_launches_python_from_special_project_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / SPECIAL_SEGMENT
            launcher_dir = root / "lib" / "script" / "app"
            entry_dir = root / "lib" / "core"
            launcher_dir.mkdir(parents=True)
            entry_dir.mkdir(parents=True)
            shutil.copy2(ROOT / "启动程序.bat", root / "启动程序.bat")
            shutil.copy2(
                ROOT / "lib" / "script" / "app" / "windows_launcher.ps1",
                launcher_dir / "windows_launcher.ps1",
            )

            pythonw = Path(sys.executable).with_name("pythonw.exe")
            self.assertTrue(pythonw.is_file())
            (root / "py.ini").write_text(
                "[Python]\n"
                f"python_executable = {sys.executable}\n"
                f"pythonw_executable = {pythonw}\n",
                encoding="utf-8",
            )
            (entry_dir / "qt_desktop_pet.py").write_text(
                "from pathlib import Path\n"
                "Path('python-started.txt').write_text('started', encoding='utf-8')\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                build_bat_restart_command(root, "normal"),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=False,
                timeout=15,
            )
            marker = root / "python-started.txt"
            deadline = time.monotonic() + 10
            while not marker.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "started")

    @unittest.skipUnless(os.name == "nt", "Windows integration test")
    def test_install_batch_runs_python_from_special_project_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / SPECIAL_SEGMENT
            root.mkdir()
            shutil.copy2(ROOT / "安装依赖.bat", root / "安装依赖.bat")
            (root / "install_deps.py").write_text(
                "from pathlib import Path\n"
                "Path('install-started.txt').write_text('started', encoding='utf-8')\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                build_bat_restart_command(root, "environment"),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=False,
                timeout=30,
            )

            marker = root / "install-started.txt"
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "started")


if __name__ == "__main__":
    unittest.main()
