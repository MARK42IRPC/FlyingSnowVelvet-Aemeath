from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from install_deps import _dsh_runtime_installer as dsh_runtime


class DshRuntimeDirectoryReplacementTests(unittest.TestCase):
    def test_windows_access_denied_is_retried(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "node.installing"
            target = Path(tmpdir) / "node"
            source.mkdir()
            original_rename = Path.rename
            attempts = 0

            def flaky_rename(path, destination):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    error = PermissionError(13, "access denied", str(path))
                    error.winerror = 5
                    raise error
                return original_rename(path, destination)

            with patch.object(dsh_runtime.os, "name", "nt"), patch.object(
                Path, "rename", flaky_rename
            ), patch.object(dsh_runtime.time, "sleep") as sleep:
                dsh_runtime._rename_with_retry(source, target)

            self.assertEqual(attempts, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertTrue(target.is_dir())

    def test_non_lock_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "missing"
            target = Path(tmpdir) / "node"
            with patch.object(dsh_runtime.time, "sleep") as sleep:
                with self.assertRaises(FileNotFoundError):
                    dsh_runtime._rename_with_retry(source, target)

            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
