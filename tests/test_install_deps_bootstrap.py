import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from install_deps import bootstrap


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstallDepsBootstrapTests(unittest.TestCase):
    def test_installer_import_does_not_require_config_or_pillow(self):
        code = r'''
import builtins

real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "config" or name.startswith("config.") or name == "PIL":
        raise AssertionError(f"unexpected early import: {name}")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import install_deps
print(install_deps.directml_config.DIRECTML_RUNTIME_VERSION)
'''
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1.22.0")

    def test_ensure_pip_fallback_downloads_executes_and_cleans_get_pip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "get-pip.py"

            def download(_url, destination):
                Path(destination).write_text("print('get-pip')", encoding="utf-8")

            failed = subprocess.CompletedProcess([], 1, stdout="")
            installed = subprocess.CompletedProcess([], 0, stdout="")
            with patch.dict("os.environ", {"TEMP": tmpdir}, clear=False), patch.object(
                bootstrap,
                "_run_python_module",
                return_value=failed,
            ), patch.object(
                bootstrap.urllib.request,
                "urlretrieve",
                side_effect=download,
            ), patch.object(
                bootstrap,
                "_run",
                return_value=installed,
            ) as run, patch.object(
                bootstrap,
                "_has_pip",
                return_value=True,
            ):
                self.assertTrue(bootstrap.ensure_pip("python.exe"))

            run.assert_called_once_with(["python.exe", str(script_path)], timeout=240)
            self.assertFalse(script_path.exists())


if __name__ == "__main__":
    unittest.main()
