"""Launch entry resolution: packaged exe first, source batch as fallback."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.app import autostart, desktop_shortcut
from lib.script.app.launch_entry import resolve_launch_entry


class ResolveLaunchEntryTests(unittest.TestCase):
    def test_prefers_packaged_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packaged = root / "启动飞行雪绒.exe"
            packaged.write_bytes(b"MZ")
            (root / "启动程序.bat").write_text("@echo off\n", encoding="utf-8")
            self.assertEqual(resolve_launch_entry(root), packaged)

    def test_falls_back_to_source_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batch = root / "启动程序.bat"
            batch.write_text("@echo off\n", encoding="utf-8")
            self.assertEqual(resolve_launch_entry(root), batch)

    def test_returns_none_without_any_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(resolve_launch_entry(Path(temp_dir)))


class ShortcutTargetTests(unittest.TestCase):
    def test_desktop_shortcut_targets_packaged_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packaged = root / "启动飞行雪绒.exe"
            packaged.write_bytes(b"MZ")
            (root / "启动程序.bat").write_text("@echo off\n", encoding="utf-8")
            desktop = root / "Desktop"
            desktop.mkdir()
            created: dict[str, str] = {}

            def create_shortcut(*, shortcut_path, target_path, **_kwargs):
                Path(shortcut_path).write_text("shortcut", encoding="utf-8")
                created["target"] = target_path
                return True, ""

            with (
                patch.object(desktop_shortcut, "_collect_desktop_paths", return_value=[str(desktop)]),
                patch.object(desktop_shortcut, "_create_shortcut_via_powershell", side_effect=create_shortcut),
                patch("lib.script.app.desktop_shortcut.STARTUP", {"ensure_desktop_shortcut": True}),
            ):
                desktop_shortcut.ensure_desktop_shortcut(str(root))

            self.assertEqual(Path(created["target"]), packaged)


class AutostartUpgradeTests(unittest.TestCase):
    def test_migrate_rewrites_legacy_batch_shortcut_to_packaged_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            startup_dir = root / "Startup"
            startup_dir.mkdir()
            shortcut_path = startup_dir / "飞行雪绒.lnk"
            shortcut_path.write_text("shortcut", encoding="utf-8")
            legacy = root / "启动程序.bat"
            legacy.write_text("@echo off\n", encoding="utf-8")
            packaged = root / "启动飞行雪绒.exe"
            packaged.write_bytes(b"MZ")
            created: dict[str, str] = {}

            def create_shortcut(*, shortcut_path, target_path, **_kwargs):
                Path(shortcut_path).write_text("shortcut", encoding="utf-8")
                created["target"] = target_path
                return True, ""

            def read_target(path):
                if Path(path) == shortcut_path:
                    return str(legacy), ""
                return str(packaged), ""

            with (
                patch.object(autostart, "get_project_root", return_value=root),
                patch.object(autostart, "get_user_startup_dir", return_value=startup_dir),
                patch.object(autostart, "get_startup_shortcut_path", return_value=shortcut_path),
                patch.object(autostart, "_get_shortcut_target", side_effect=read_target),
                patch.object(autostart, "_create_shortcut_via_powershell", side_effect=create_shortcut),
                patch.object(autostart, "_remove_legacy_registry_value"),
            ):
                autostart.migrate_legacy_autostart()
                self.assertTrue(autostart.is_autostart_enabled())

            self.assertEqual(Path(created["target"]), packaged)


if __name__ == "__main__":
    unittest.main()
