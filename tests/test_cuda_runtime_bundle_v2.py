from __future__ import annotations

import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from lib.core import cuda_runtime_bundle_v2 as bundle


class CudaRuntimeBundleV2Tests(unittest.TestCase):
    def _fake_payload(self, root: Path) -> None:
        files = set(bundle._required_payload_paths())
        files.update(
            {
                "python/Lib/encodings/__init__.py",
                "runtime/Lib/site-packages/numpy/__init__.py",
            }
        )
        for relative in sorted(files):
            path = root / Path(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == bundle.CUDA_RUNTIME_V2_WORKER_ENTRY:
                path.write_text("print('worker')\n", encoding="utf-8")
            elif relative.endswith(".py"):
                path.write_text("# bundled\n", encoding="utf-8")
            else:
                path.write_bytes(relative.encode("ascii"))

    def test_manifest_declares_bundled_components_and_driver_only_prerequisite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "payload"
            self._fake_payload(root)
            bundle.write_worker_launchers(root)
            manifest = bundle.build_manifest(root)

            self.assertEqual(manifest["format_version"], 2)
            self.assertEqual(manifest["python"]["executable"], "python/python.exe")
            self.assertEqual(manifest["onnxruntime"]["provider"], "CUDAExecutionProvider")
            self.assertEqual(
                manifest["external_prerequisites"],
                [
                    {
                        "kind": "nvidia_display_driver",
                        "minimum_version": bundle.CUDA_RUNTIME_V2_MIN_DRIVER_VERSION,
                        "network_required": False,
                    }
                ],
            )
            self.assertTrue(
                "python/python.exe"
                in {item["path"] for item in manifest["files"]}
            )
            bundle.validate_manifest(manifest)

    def test_build_is_deterministic_and_verifies_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "payload"
            self._fake_payload(source)
            first = root / "first.zip"
            second = root / "second.zip"
            first_summary = bundle.build_bundle(source, first)
            second_summary = bundle.build_bundle(source, second)

            self.assertEqual(first_summary["archive_sha256"], second_summary["archive_sha256"])
            self.assertEqual(first_summary["bundle_id"], second_summary["bundle_id"])
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(archive.namelist()[0], "manifest.json")
                self.assertIn("SHA256SUMS.txt", archive.namelist())
                self.assertTrue(
                    all(name.startswith(("manifest.json", "SHA256SUMS.txt", "payload/")) for name in archive.namelist())
                )

            result = bundle.verify_archive_offline(
                first,
                expected_archive_sha256=str(first_summary["archive_sha256"]),
                driver_version="551.23",
            )
            self.assertEqual(result.bundle_id, first_summary["bundle_id"])
            self.assertEqual(result.detected_driver, "551.23")
            self.assertEqual(
                result.worker_command[0].replace("\\", "/").split("/")[-2:],
                ["python", "python.exe"],
            )

    def test_launcher_isolated_from_system_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "payload"
            self._fake_payload(root)
            bundle.write_worker_launchers(root)
            command, environment = bundle.worker_launch_command(
                root,
                ("--stdio",),
                base_environment={
                    "PATH": "ambient",
                    "PYTHONPATH": "ambient",
                    "PYTHONHOME": "ambient-home",
                    "PYTHONUSERBASE": "ambient-user",
                    "PYENV_ROOT": "ambient-pyenv",
                    "CONDA_PREFIX": "ambient-conda",
                    "CONDA_DEFAULT_ENV": "ambient-env",
                    "VIRTUAL_ENV": "ambient-venv",
                    "CUDA_HOME": "ambient-cuda",
                    "CUDNN_PATH": "ambient-cudnn",
                    "LD_LIBRARY_PATH": "ambient-ld",
                },
            )
            self.assertTrue(command[0].replace("\\", "/").endswith("/python/python.exe"))
            self.assertTrue(command[1] == "-I")
            self.assertTrue(command[2].replace("\\", "/").endswith("/worker/launch_cuda_worker.py"))
            self.assertEqual(command[3], "--stdio")
            self.assertIn(
                str(root / "runtime" / "Lib" / "site-packages"),
                environment.get("PYTHONPATH", ""),
            )
            self.assertTrue(environment["PATH"].startswith(str(root / "python")))
            self.assertNotIn("ambient", environment["PATH"])
            self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
            self.assertNotIn("PYTHONHOME_ORIGINAL", environment)
            self.assertNotIn("PYTHONUSERBASE", environment)
            self.assertNotIn("PYENV_ROOT", environment)
            self.assertNotIn("CONDA_PREFIX", environment)
            self.assertNotIn("CONDA_DEFAULT_ENV", environment)
            self.assertNotIn("VIRTUAL_ENV", environment)
            self.assertNotIn("CUDA_HOME", environment)
            self.assertNotIn("CUDNN_PATH", environment)
            self.assertNotIn("LD_LIBRARY_PATH", environment)
            cmd_text = (root / Path(*bundle.CUDA_RUNTIME_V2_WORKER_LAUNCHER.split("/"))).read_text(encoding="ascii")
            self.assertIn("python\\python.exe\" -i", cmd_text.lower())
            self.assertIn("launch_cuda_worker.py", cmd_text.lower())
            self.assertNotIn("where python", cmd_text.lower())

    def test_driver_requirement_rejects_old_driver_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "payload"
            self._fake_payload(root)
            manifest = bundle.build_manifest(root, minimum_driver_version="551.00")
            with self.assertRaises(bundle.CudaRuntimeV2Error):
                bundle.verify_driver_requirement(manifest, driver_version="550.99")
            self.assertIsNone(bundle.verify_driver_requirement(manifest))
            self.assertTrue(bundle.driver_version_satisfies("551.00.12", "551.00"))

    def test_payload_hash_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "payload"
            self._fake_payload(source)
            archive = root / "runtime.zip"
            bundle.build_bundle(source, archive)
            extracted = root / "extracted"
            bundle.safe_extract_bundle(archive, extracted)
            target = extracted / bundle.CUDA_RUNTIME_V2_PAYLOAD_ROOT / Path(
                *bundle.CUDA_RUNTIME_V2_ORT_PROVIDER.split("/")
            )
            target.write_bytes(b"tampered")
            with self.assertRaises(bundle.CudaRuntimeV2Error):
                bundle.validate_payload_tree(extracted)

    def test_safe_extract_rejects_traversal_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            traversal = root / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("../escape.txt", b"bad")
            with self.assertRaises(bundle.CudaRuntimeV2Error):
                bundle.safe_extract_bundle(traversal, root / "out")

            duplicate = root / "duplicate.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(duplicate, "w") as archive:
                    archive.writestr("manifest.json", b"{}")
                    archive.writestr("manifest.json", b"{}")
            with self.assertRaises(bundle.CudaRuntimeV2Error):
                bundle.safe_extract_bundle(duplicate, root / "out-duplicate")

            for unsafe_name in ("a//b.txt", "a/./b.txt", "a/../b.txt", "C:/b.txt"):
                malformed = root / ("unsafe-" + str(len(unsafe_name)) + ".zip")
                with zipfile.ZipFile(malformed, "w") as archive:
                    archive.writestr(unsafe_name, b"bad")
                with self.assertRaises(bundle.CudaRuntimeV2Error):
                    bundle.safe_extract_bundle(malformed, root / ("out-" + str(len(unsafe_name))))

    def test_manifest_rejects_second_external_prerequisite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "payload"
            self._fake_payload(root)
            manifest = bundle.build_manifest(root)
            manifest["external_prerequisites"].append({"kind": "system_python"})
            with self.assertRaises(bundle.CudaRuntimeV2Error):
                bundle.validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
