"""Regression tests: startup helpers must never flash a console window."""

from __future__ import annotations

import os
import subprocess
import unittest
from unittest.mock import patch

from lib.core.process_utils import hidden_process_kwargs
from lib.script.app import desktop_shortcut, startup_probe


class HiddenProcessKwargsTests(unittest.TestCase):
    def test_non_windows_returns_no_flags(self):
        with patch("lib.core.process_utils.os.name", "posix"):
            self.assertEqual(hidden_process_kwargs(), {})

    def test_windows_helper_kwargs_hide_console(self):
        if os.name != "nt":
            self.skipTest("console suppression is Windows specific")
        kwargs = hidden_process_kwargs()
        self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
        startupinfo = kwargs["startupinfo"]
        self.assertTrue(startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(startupinfo.wShowWindow, subprocess.SW_HIDE)

    def _assert_hidden(self, kwargs):
        if os.name != "nt":
            self.skipTest("console suppression is Windows specific")
        self.assertIn("creationflags", kwargs)
        self.assertIn("startupinfo", kwargs)

    def test_startup_probe_powershell_probe_is_hidden(self):
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with patch.object(startup_probe.subprocess, "run", return_value=completed) as run:
            startup_probe._run_capture_text(["powershell.exe"], 1)
        self._assert_hidden(run.call_args.kwargs)

    def test_desktop_shortcut_powershell_probe_is_hidden(self):
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with patch.object(desktop_shortcut.subprocess, "run", return_value=completed) as run:
            desktop_shortcut._run_capture_text(["powershell.exe"], 1)
        self._assert_hidden(run.call_args.kwargs)

    def test_office_node_probe_is_hidden(self):
        from lib.script.office import runtime

        completed = subprocess.CompletedProcess([], 0, "v24.13.0", "")
        with (
            patch.object(runtime, "bundled_node_executable", return_value=runtime.project_root() / "missing-node.exe"),
            patch.object(runtime.shutil, "which", return_value="node.exe"),
            patch.object(runtime.subprocess, "run", return_value=completed) as run,
        ):
            runtime.resolve_node_executable()
        self._assert_hidden(run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
