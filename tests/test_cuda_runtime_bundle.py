import json
import tempfile
import unittest
import warnings
import zipfile
import subprocess
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lib.core import voice_runtime_contract as contract
from lib.core.cuda_runtime_bundle import (
    CudaBundleError,
    safe_extract_zip,
    validate_bundle_tree,
)
from install_deps import voice_runtime as runtime_installer
from scripts import build_cuda_voice_runtime as builder


class CudaRuntimeBundleTests(unittest.TestCase):
    def _fake_source(self, root: Path):
        site = root / "Lib" / "site-packages"
        ort = site / "onnxruntime"
        for relative in contract.CUDA_RUNTIME_BUNDLE_ORT_FILES:
            path = ort / Path(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == "__init__.py":
                path.write_text('__version__ = "1.22.0"\n', encoding="utf-8")
            else:
                path.write_bytes(relative.encode("ascii"))
        for name in contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS:
            path = site / "nvidia" / "test" / "bin" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode("ascii"))
        license_dir = site / "nvidia_test-1.0.dist-info" / "licenses"
        license_dir.mkdir(parents=True, exist_ok=True)
        (license_dir / "License.txt").write_text("test license\n", encoding="utf-8")

    def test_builder_creates_manifest_checksums_and_deterministic_archive(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "source"
            self._fake_source(source)
            first = root / "first.zip"
            second = root / "second.zip"
            summary1 = builder.build_bundle(source, first)
            summary2 = builder.build_bundle(source, second)

            self.assertEqual(summary1["archive_sha256"], summary2["archive_sha256"])
            self.assertEqual(summary1["payload_files"], len(contract.CUDA_RUNTIME_BUNDLE_ORT_FILES) + len(contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS))
            with zipfile.ZipFile(first) as archive:
                self.assertIn("bundle.json", archive.namelist())
                self.assertIn("SHA256SUMS.txt", archive.namelist())
                self.assertTrue(any(name.startswith("payload/") for name in archive.namelist()))
                extracted = root / "extracted"
                safe_extract_zip(first, extracted)
            manifest = validate_bundle_tree(extracted)
            self.assertEqual(manifest["bundle_id"], summary1["bundle_id"])

    def test_safe_extract_rejects_parent_path(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            archive_path = root / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.txt", b"bad")
            with self.assertRaises(CudaBundleError):
                safe_extract_zip(archive_path, root / "out")

    def test_safe_extract_rejects_duplicate_member(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            archive_path = root / "bad.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr("bundle.json", b"{}")
                    archive.writestr("bundle.json", b"{}")
            with self.assertRaises(CudaBundleError):
                safe_extract_zip(archive_path, root / "out")

    def test_builder_requires_all_runtime_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "source"
            self._fake_source(source)
            missing = source / "Lib" / "site-packages" / "onnxruntime" / "capi" / "onnxruntime.dll"
            missing.unlink()
            with self.assertRaises(builder.BundleBuildError):
                builder.collect_payload(source)

    def test_validator_rejects_unlisted_payload_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "source"
            self._fake_source(source)
            archive = root / "runtime.zip"
            builder.build_bundle(source, archive)
            extracted = root / "extracted"
            safe_extract_zip(archive, extracted)
            extra = extracted / "payload" / "Lib" / "site-packages" / "extra.dll"
            extra.write_bytes(b"unexpected")
            with self.assertRaises(CudaBundleError):
                validate_bundle_tree(extracted)

    def test_bundle_contract_paths_are_relative(self):
        self.assertFalse(Path(contract.CUDA_RUNTIME_BUNDLE_DLL_DIRECTORY).is_absolute())
        for name in contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS:
            self.assertNotIn("/", name)

    def test_runtime_readiness_requires_pinned_bundle_hash_and_full_dll_set(self):
        with tempfile.TemporaryDirectory() as tempdir, patch.dict(
            "os.environ",
            {"AEMEATH_DESK_PET_HOME": tempdir},
        ):
            root = contract.get_cuda_runtime_root()
            (root / "Scripts").mkdir(parents=True)
            (root / "Scripts" / "python.exe").write_bytes(b"python")
            provider = root / "Lib" / "site-packages" / "onnxruntime" / "capi" / "onnxruntime_providers_cuda.dll"
            provider.parent.mkdir(parents=True)
            provider.write_bytes(b"provider")
            dll_dir = contract.get_cuda_bundle_dll_dir(root)
            dll_dir.mkdir(parents=True)
            for name in contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS:
                (dll_dir / name).write_bytes(b"dll")
            marker = {
                "runtime": "onnxruntime-gpu",
                "version": contract.CUDA_RUNTIME_VERSION,
                "abi": contract.CUDA_RUNTIME_ABI,
                "provider": "CUDAExecutionProvider",
                "source": "bundle",
                "bundle_format": contract.CUDA_RUNTIME_BUNDLE_FORMAT,
                "bundle_version": contract.CUDA_RUNTIME_BUNDLE_FORMAT_VERSION,
                "bundle_id": "test-bundle-id",
                "archive_sha256": contract.CUDA_RUNTIME_BUNDLE_SHA256,
                "required_dlls": list(contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS),
            }
            (root / "runtime.json").write_text(json.dumps(marker), encoding="utf-8")
            (root / "bundle.json").write_text(json.dumps({
                "format": contract.CUDA_RUNTIME_BUNDLE_FORMAT,
                "format_version": contract.CUDA_RUNTIME_BUNDLE_FORMAT_VERSION,
                "python_abi": contract.CUDA_RUNTIME_ABI,
                "onnxruntime_version": contract.CUDA_RUNTIME_VERSION,
                "bundle_id": "test-bundle-id",
            }), encoding="utf-8")
            self.assertTrue(contract.is_cuda_runtime_ready())
            marker["archive_sha256"] = "0" * 64
            (root / "runtime.json").write_text(json.dumps(marker), encoding="utf-8")
            self.assertFalse(contract.is_cuda_runtime_ready())

    def test_legacy_pip_runtime_marker_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tempdir, patch.dict(
            "os.environ",
            {"AEMEATH_DESK_PET_HOME": tempdir},
        ):
            root = contract.get_cuda_runtime_root()
            (root / "Scripts").mkdir(parents=True)
            (root / "Scripts" / "python.exe").write_bytes(b"python")
            provider = contract.get_cuda_provider_dll_path(root)
            provider.parent.mkdir(parents=True)
            provider.write_bytes(b"provider")
            (root / "runtime.json").write_text(json.dumps({
                "runtime": "onnxruntime-gpu",
                "version": contract.CUDA_RUNTIME_VERSION,
                "abi": contract.CUDA_RUNTIME_ABI,
                "provider": "CUDAExecutionProvider",
                "source": "pip",
            }), encoding="utf-8")

            self.assertFalse(contract.is_cuda_runtime_ready())

    def test_installer_validates_and_atomically_activates_bundle(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "source"
            self._fake_source(source)
            archive = root / "runtime.zip"
            builder.build_bundle(source, archive)
            digest = builder.sha256_file(archive)
            target = root / "active"

            def fake_run(command, timeout=12, **kwargs):
                if "venv" not in command:
                    raise AssertionError(command)
                venv_root = Path(command[-1])
                (venv_root / "Scripts").mkdir(parents=True, exist_ok=True)
                (venv_root / "Scripts" / "python.exe").write_bytes(b"python")
                (venv_root / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(command, 0, stdout="")

            def fake_download(_url, destination, **kwargs):
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(archive, destination)

            with patch.object(runtime_installer.directml_config, "CUDA_RUNTIME_BUNDLE_SHA256", digest), patch.object(
                runtime_installer, "_stream_download_with_progress", side_effect=fake_download
            ), patch.object(runtime_installer, "_run", side_effect=fake_run), patch.object(
                runtime_installer, "_cuda_runtime_probe", return_value=(True, "")
            ), patch.object(
                runtime_installer.directml_config, "is_cuda_runtime_ready", return_value=True
            ):
                ready, detail = runtime_installer._install_cuda_runtime_bundle(
                    "python.exe",
                    target,
                    urls=("file:///runtime.zip",),
                )

            self.assertTrue(ready)
            self.assertEqual(detail, "")
            marker = json.loads((target / "runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["source"], "bundle")
            self.assertEqual(marker["archive_sha256"], digest)
            self.assertTrue(
                (target / Path(*contract.CUDA_RUNTIME_BUNDLE_DLL_DIRECTORY.split("/")) / contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS[0]).is_file()
            )

    def test_probe_payload_ignores_native_diagnostics(self):
        payload = runtime_installer._parse_probe_payload(
            "native warning\n{\"providers\": [\"CUDAExecutionProvider\"]}\n"
        )
        self.assertEqual(payload["providers"], ["CUDAExecutionProvider"])

    def test_installer_checks_temporary_disk_space_before_download(self):
        with tempfile.TemporaryDirectory() as tempdir, patch.object(
            runtime_installer.directml_config,
            "CUDA_RUNTIME_BUNDLE_SHA256",
            "a" * 64,
        ), patch.object(
            runtime_installer.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=1),
        ), patch.object(runtime_installer, "_stream_download_with_progress") as download:
            ready, detail = runtime_installer._install_cuda_runtime_bundle(
                "python.exe",
                Path(tempdir) / "active",
                urls=("https://example.invalid/runtime.zip",),
            )
        self.assertFalse(ready)
        self.assertIn("空间不足", detail)
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
