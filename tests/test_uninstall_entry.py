"""Tests for locating and launching the packaged uninstaller."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.script.app import uninstall_entry
from lib.script.app.launch_entry import (
    PACKAGED_LAUNCHER_NAME,
    SOURCE_LAUNCH_SCRIPT_NAME,
)


class ResolveUninstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="fsv-uninstall-entry-")
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _packaged_layout(self) -> Path:
        launcher = self.root / PACKAGED_LAUNCHER_NAME
        launcher.write_bytes(b"stub")
        return launcher

    def _uninstaller(self) -> Path:
        uninstaller = self.root / uninstall_entry.UNINSTALLER_FILE_NAME
        uninstaller.write_bytes(b"stub")
        return uninstaller

    def test_source_checkout_has_no_uninstaller(self) -> None:
        (self.root / SOURCE_LAUNCH_SCRIPT_NAME).write_text("py lib/core/qt_desktop_pet.py\n", encoding="utf-8")

        self.assertIsNone(uninstall_entry.resolve_uninstaller(self.root))

    def test_packaged_layout_without_the_binary_reports_nothing(self) -> None:
        self._packaged_layout()

        self.assertIsNone(uninstall_entry.resolve_uninstaller(self.root))

    def test_packaged_layout_returns_the_shipped_uninstaller(self) -> None:
        self._packaged_layout()
        uninstaller = self._uninstaller()

        self.assertEqual(uninstall_entry.resolve_uninstaller(self.root), uninstaller)

    def test_packaged_uninstaller_path_sits_next_to_the_launcher(self) -> None:
        self.assertEqual(
            uninstall_entry.packaged_uninstaller_path(self.root),
            self.root / "卸载飞行雪绒.exe",
        )

    def test_launch_command_is_the_absolute_binary_path(self) -> None:
        self._packaged_layout()
        uninstaller = self._uninstaller()

        command = uninstall_entry.uninstaller_launch_command(uninstaller)

        self.assertEqual(command, [str(uninstaller.resolve())])

    def test_launch_uninstaller_detaches_the_process(self) -> None:
        uninstaller = self._uninstaller()
        with patch.object(uninstall_entry.subprocess, "Popen") as popen:
            uninstall_entry.launch_uninstaller(uninstaller)

        command, kwargs = popen.call_args
        self.assertEqual(command[0], [str(uninstaller.resolve())])
        self.assertIn("creationflags", kwargs)
        self.assertEqual(kwargs["stdin"], uninstall_entry.subprocess.DEVNULL)

    def test_launch_uninstaller_retries_without_process_group_breakaway(self) -> None:
        uninstaller = self._uninstaller()
        with patch.object(
            uninstall_entry.subprocess,
            "Popen",
            side_effect=[OSError("job"), object()],
        ) as popen:
            uninstall_entry.launch_uninstaller(uninstaller)

        self.assertEqual(popen.call_count, 2)
        first_flags = popen.call_args_list[0].kwargs["creationflags"]
        second_flags = popen.call_args_list[1].kwargs["creationflags"]
        self.assertNotEqual(first_flags, second_flags)


if __name__ == "__main__":
    unittest.main()
