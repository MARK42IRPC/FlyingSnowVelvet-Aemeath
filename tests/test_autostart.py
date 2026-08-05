from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.app import autostart


class AutostartTests(unittest.TestCase):
    def test_source_launch_path_uses_project_root(self):
        root = Path(__file__).resolve().parents[1]
        with patch.object(autostart.sys, "frozen", False, create=True):
            self.assertEqual(autostart.get_launch_script_path(), root / "启动程序.bat")

    def test_user_startup_dir_is_derived_from_appdata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(autostart.os.environ, {"APPDATA": temp_dir}, clear=False):
                self.assertEqual(
                    autostart.get_user_startup_dir(),
                    Path(temp_dir) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup",
                )

    def test_enable_writes_and_verifies_shortcut_before_replacing_final_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            startup_dir = root / "Startup"
            launch_script = root / "启动程序.bat"
            launch_script.write_text("@echo off\n", encoding="utf-8")
            final_path = startup_dir / "飞行雪绒.lnk"

            def create_shortcut(*, shortcut_path, **_kwargs):
                Path(shortcut_path).write_text("shortcut", encoding="utf-8")
                return True, ""

            with (
                patch.object(autostart, "get_user_startup_dir", return_value=startup_dir),
                patch.object(autostart, "get_launch_script_path", return_value=launch_script),
                patch.object(autostart, "_create_shortcut_via_powershell", side_effect=create_shortcut),
                patch.object(autostart, "_get_shortcut_target", return_value=(str(launch_script), "")),
                patch.object(autostart, "_remove_legacy_registry_value") as remove_legacy,
            ):
                ok, message = autostart.enable_autostart()

            self.assertTrue(ok, message)
            self.assertTrue(final_path.is_file())
            remove_legacy.assert_called_once_with()

    def test_enable_rejects_shortcut_with_wrong_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            startup_dir = root / "Startup"
            launch_script = root / "启动程序.bat"
            launch_script.write_text("@echo off\n", encoding="utf-8")
            wrong_target = root / "other.bat"
            wrong_target.write_text("@echo off\n", encoding="utf-8")

            def create_shortcut(*, shortcut_path, **_kwargs):
                Path(shortcut_path).write_text("shortcut", encoding="utf-8")
                return True, ""

            with (
                patch.object(autostart, "get_user_startup_dir", return_value=startup_dir),
                patch.object(autostart, "get_launch_script_path", return_value=launch_script),
                patch.object(autostart, "_create_shortcut_via_powershell", side_effect=create_shortcut),
                patch.object(autostart, "_get_shortcut_target", return_value=(str(wrong_target), "")),
            ):
                ok, message = autostart.enable_autostart()

            self.assertFalse(ok)
            self.assertIn("校验", message)
            self.assertFalse((startup_dir / "飞行雪绒.lnk").exists())

    def test_disable_removes_shortcut_and_legacy_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            startup_dir = Path(temp_dir) / "Startup"
            startup_dir.mkdir(parents=True)
            (startup_dir / "飞行雪绒.lnk").write_text("shortcut", encoding="utf-8")

            with (
                patch.object(autostart, "get_startup_shortcut_path", return_value=startup_dir / "飞行雪绒.lnk"),
                patch.object(autostart, "_remove_legacy_registry_value") as remove_legacy,
                patch.object(autostart, "_read_legacy_registry_value", return_value=None),
            ):
                ok, message = autostart.disable_autostart()

            self.assertTrue(ok, message)
            self.assertFalse((startup_dir / "飞行雪绒.lnk").exists())
            remove_legacy.assert_called_once_with()

    def test_migrate_valid_legacy_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            launch_script = Path(temp_dir) / "启动程序.bat"
            launch_script.write_text("@echo off\n", encoding="utf-8")
            with (
                patch.object(autostart, "is_autostart_enabled", return_value=False),
                patch.object(
                    autostart,
                    "_read_legacy_registry_value",
                    return_value=f'"{launch_script}"',
                ),
                patch.object(autostart, "get_launch_script_path", return_value=launch_script),
                patch.object(autostart, "enable_autostart", return_value=(True, "")) as enable,
            ):
                autostart.migrate_legacy_autostart()

            enable.assert_called_once_with()

    @unittest.skipUnless(os.name == "nt", "Windows integration test")
    def test_real_startup_shortcut_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            startup_dir = Path(temp_dir) / "Startup"
            with (
                patch.object(autostart, "get_user_startup_dir", return_value=startup_dir),
                patch.object(autostart, "_remove_legacy_registry_value"),
                patch.object(autostart, "_read_legacy_registry_value", return_value=None),
            ):
                enabled, message = autostart.enable_autostart()
                self.assertTrue(enabled, message)
                self.assertTrue(autostart.is_autostart_enabled())
                disabled, message = autostart.disable_autostart()

            self.assertTrue(disabled, message)
            self.assertFalse((startup_dir / "飞行雪绒.lnk").exists())


if __name__ == "__main__":
    unittest.main()
