import hashlib
import io
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

            def download(destination):
                Path(destination).write_text("print('get-pip')", encoding="utf-8")
                return "https://mirror.invalid/get-pip.py"

            failed = subprocess.CompletedProcess([], 1, stdout="")
            installed = subprocess.CompletedProcess([], 0, stdout="")
            with patch.dict("os.environ", {"TEMP": tmpdir}, clear=False), patch.object(
                bootstrap,
                "_run_python_module",
                return_value=failed,
            ), patch.object(
                bootstrap,
                "_download_get_pip",
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

    def test_get_pip_download_falls_back_and_uses_timeout(self):
        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            bootstrap,
            "GET_PIP_URLS",
            ("https://first.invalid/get-pip.py", "https://second.invalid/get-pip.py"),
        ), patch.object(
            bootstrap.urllib.request,
            "urlopen",
            side_effect=[OSError("blocked"), FakeResponse(b"print('pip')")],
        ) as urlopen:
            destination = Path(tmpdir) / "get-pip.py"
            source = bootstrap._download_get_pip(destination)
            downloaded = destination.read_bytes()

        self.assertEqual(source, "https://second.invalid/get-pip.py")
        self.assertEqual(downloaded, b"print('pip')")
        self.assertEqual(urlopen.call_count, 2)
        self.assertTrue(
            all(call.kwargs["timeout"] == bootstrap.GET_PIP_DOWNLOAD_TIMEOUT for call in urlopen.call_args_list)
        )

    def test_batch_python_bootstrap_uses_fixed_hash_and_powershell_helper(self):
        content = (PROJECT_ROOT / "安装依赖.bat").read_text(encoding="utf-8")

        self.assertIn("install_deps\\python_bootstrap.ps1", content)
        self.assertIn(
            "8d0fd1c7bab34dd26fb89327cf7b7c2c7dc57c4d2a7bea58eae198aa9dd5b4ef",
            content,
        )
        self.assertNotIn("Invoke-WebRequest", content)

    def test_powershell_bootstrap_resolves_base_urls_without_python(self):
        helper = PROJECT_ROOT / "install_deps" / "python_bootstrap.ps1"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "space & bang! 100% O'Brien"
            root.mkdir()
            manifest = root / "resc.net.txt"
            manifest.write_text(
                "https://gitee.example/releases/RESC/\n"
                "https://github.example/releases/RESC/\n"
                "python-3.11.6-amd64.exe\n",
                encoding="utf-8",
            )
            target = root / "python.exe"
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-ManifestPath",
                    str(manifest),
                    "-ResourceName",
                    "python-3.11.6-amd64.exe",
                    "-TargetPath",
                    str(target),
                    "-ExpectedSha256",
                    "0" * 64,
                    "-ResolveOnly",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "https://gitee.example/releases/RESC/python-3.11.6-amd64.exe",
                "https://github.example/releases/RESC/python-3.11.6-amd64.exe",
            ],
        )

    def test_powershell_bootstrap_accepts_existing_verified_installer(self):
        helper = PROJECT_ROOT / "install_deps" / "python_bootstrap.ps1"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = b"verified-python-installer"
            target = root / "python.exe"
            target.write_bytes(payload)
            manifest = root / "resc.net.txt"
            manifest.write_text(
                "https://invalid.example/RESC/\npython-3.11.6-amd64.exe\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-ManifestPath",
                    str(manifest),
                    "-ResourceName",
                    "python-3.11.6-amd64.exe",
                    "-TargetPath",
                    str(target),
                    "-ExpectedSha256",
                    hashlib.sha256(payload).hexdigest(),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Verified existing Python installer", result.stdout)

    def test_powershell_bootstrap_reuses_completed_part_file(self):
        helper = PROJECT_ROOT / "install_deps" / "python_bootstrap.ps1"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = b"completed-partial-download"
            target = root / "python.exe"
            target.with_name(target.name + ".part").write_bytes(payload)
            manifest = root / "resc.net.txt"
            manifest.write_text(
                "https://invalid.example/RESC/\npython-3.11.6-amd64.exe\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-ManifestPath",
                    str(manifest),
                    "-ResourceName",
                    "python-3.11.6-amd64.exe",
                    "-TargetPath",
                    str(target),
                    "-ExpectedSha256",
                    hashlib.sha256(payload).hexdigest(),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
            installed_payload = target.read_bytes() if target.exists() else b""

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(installed_payload, payload)
        self.assertIn("Reused and verified completed partial download", result.stdout)


if __name__ == "__main__":
    unittest.main()
