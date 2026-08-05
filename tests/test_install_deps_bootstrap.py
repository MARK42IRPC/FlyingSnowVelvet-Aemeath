import subprocess
import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
