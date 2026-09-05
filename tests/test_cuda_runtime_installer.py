import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.core import voice_runtime_contract as contract
from lib.core.cuda_runtime_cleanup import cleanup_obsolete_cuda_runtime_artifacts
from lib.core.cuda_runtime_installer import (
    CudaRuntimeInstallCancelled,
    CudaRuntimeInstaller,
)
from scripts import build_cuda_voice_runtime as builder


class CudaRuntimeInstallerTests(unittest.TestCase):
    def _fake_source(self, root: Path) -> None:
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
            path = site / "nvidia" / "runtime" / "bin" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode("ascii"))
        license_dir = site / "nvidia_runtime-1.0.dist-info" / "licenses"
        license_dir.mkdir(parents=True, exist_ok=True)
        (license_dir / "License.txt").write_text("license\n", encoding="utf-8")

    def test_bundle_installer_activates_only_after_probes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            shared = Path(tempdir) / "AemeathDeskPet"
            source = Path(tempdir) / "source"
            self._fake_source(source)
            archive = Path(tempdir) / "runtime.zip"
            summary = builder.build_bundle(source, archive)
            target = (
                shared
                / "voice"
                / "runtimes"
                / "onnx-cuda"
                / f"{contract.CUDA_RUNTIME_VERSION}-{contract.CUDA_RUNTIME_ABI}"
            )
            progress = []
            voice_probe_calls = []

            def voice_probe(runtime_python, cancel_event):
                voice_probe_calls.append(Path(runtime_python))
                self.assertFalse(cancel_event.is_set())
                return True, ""

            installer = CudaRuntimeInstaller(
                "python.exe",
                target_root=target,
                urls=(archive.as_uri(),),
                progress_callback=lambda *args: progress.append(args),
                voice_probe=voice_probe,
            )

            def fake_check_python():
                return None

            def fake_run(command, *, timeout):
                if "venv" in command:
                    root = Path(command[-1])
                    (root / "Scripts").mkdir(parents=True, exist_ok=True)
                    (root / "Scripts" / "python.exe").write_bytes(b"python")
                    (root / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
                    return 0, "", ""
                return 0, json.dumps({
                    "python": [3, 11],
                    "bits": 64,
                    "version": contract.CUDA_RUNTIME_VERSION,
                    "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
                }), ""

            with patch.dict(
                "os.environ",
                {"AEMEATH_DESK_PET_HOME": str(shared)},
            ), patch.object(
                contract, "CUDA_RUNTIME_BUNDLE_SHA256", summary["archive_sha256"]
            ), patch.object(
                contract, "CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES", archive.stat().st_size
            ), patch.object(
                contract, "CUDA_RUNTIME_BUNDLE_PAYLOAD_BYTES", summary["payload_bytes"]
            ), patch.object(
                installer, "_check_python", side_effect=fake_check_python
            ), patch.object(
                installer, "_run_process", side_effect=fake_run
            ):
                result = installer.install()
                self.assertTrue(contract.is_cuda_runtime_ready(target))

            self.assertEqual(result.runtime_root, target)
            self.assertEqual(len(voice_probe_calls), 1)
            self.assertEqual(progress[-1][:3], ("install", 1000, 1000))
            self.assertFalse(any(path.name.startswith(f".{target.name}.") for path in target.parent.iterdir()))

    def test_cancelled_install_does_not_create_runtime(self):
        with tempfile.TemporaryDirectory() as tempdir:
            shared = Path(tempdir) / "AemeathDeskPet"
            target = (
                shared
                / "voice"
                / "runtimes"
                / "onnx-cuda"
                / f"{contract.CUDA_RUNTIME_VERSION}-{contract.CUDA_RUNTIME_ABI}"
            )
            installer = CudaRuntimeInstaller("python.exe", target_root=target)
            installer.cancel()
            with self.assertRaises(CudaRuntimeInstallCancelled):
                installer.install()
            self.assertFalse(target.exists())

    def test_startup_cleanup_preserves_valid_current_bundle_and_removes_residue(self):
        with tempfile.TemporaryDirectory() as tempdir:
            shared = Path(tempdir) / "AemeathDeskPet"
            parent = shared / "voice" / "runtimes" / "onnx-cuda"
            current = parent / f"{contract.CUDA_RUNTIME_VERSION}-{contract.CUDA_RUNTIME_ABI}"
            old = parent / "1.21.0-cp311-win_amd64"
            staging = parent / ".1.22.0-cp311-win_amd64.installing-old"
            current.mkdir(parents=True)
            old.mkdir()
            staging.mkdir()
            archive = parent / "runtime.zip"
            partial = parent / "runtime.zip.part"
            archive.write_bytes(b"zip")
            partial.write_bytes(b"part")

            with patch.dict(
                "os.environ",
                {"AEMEATH_DESK_PET_HOME": str(shared)},
            ), patch.object(
                contract,
                "is_cuda_runtime_ready",
                side_effect=lambda path=None: Path(path or current) == current,
            ):
                report = cleanup_obsolete_cuda_runtime_artifacts(parent)

            self.assertTrue(current.is_dir())
            self.assertFalse(old.exists())
            self.assertFalse(staging.exists())
            self.assertFalse(archive.exists())
            self.assertFalse(partial.exists())
            self.assertEqual(report.errors, ())

    def test_cleanup_rejects_non_managed_directory(self):
        with tempfile.TemporaryDirectory() as tempdir:
            unrelated = Path(tempdir) / "downloads"
            unrelated.mkdir()
            payload = unrelated / "runtime.zip"
            payload.write_bytes(b"keep")

            report = cleanup_obsolete_cuda_runtime_artifacts(unrelated)

            self.assertTrue(payload.is_file())
            self.assertTrue(report.errors)


if __name__ == "__main__":
    unittest.main()
