import hashlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lib.script.gsvmove import package_manager as manager
from lib.script.gsvmove import service as service_module
from lib.script.gsvmove.package_manager import (
    VOICE_PACKAGE_FORMAT,
    VOICE_PACKAGE_FORMAT_VERSION,
    VOICE_PACKAGE_RUNTIME_REVISION,
    VoicePackageError,
    VoicePackageCancelled,
    VoicePackageInstaller,
    get_voice_package_urls,
    remove_legacy_gsvmove_runtime,
    remove_voice_package,
    validate_voice_package,
)
from lib.script.gsvmove.rar_backend import ensure_bundled_unrar


def _create_fake_package(
    root: Path,
    *,
    g2pw_model_name: str | None = "g2pW_full_precision.onnx",
) -> Path:
    manifest = {
        "format": VOICE_PACKAGE_FORMAT,
        "format_version": VOICE_PACKAGE_FORMAT_VERSION,
        "runtime_revision": VOICE_PACKAGE_RUNTIME_REVISION,
        "name": "aimisiV2",
        "sample_rate": 32000,
        "languages": ["zh", "en"],
        "precision_profile": "fp16",
    }
    required_files = manager._REQUIRED_PACKAGE_FILES + manager._PROFILE_REQUIRED_PACKAGE_FILES["fp16"]
    if g2pw_model_name:
        required_files += (f"{manager._G2PW_MODEL_DIR}/{g2pw_model_name}",)
    for relative in required_files:
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == "manifest.json":
            path.write_text(json.dumps(manifest), encoding="utf-8")
        elif relative == "SHA256SUMS.txt":
            continue
        else:
            path.write_bytes(f"fixture:{relative}".encode("utf-8"))

    checksum_lines = []
    for relative in required_files:
        if relative == "SHA256SUMS.txt":
            continue
        path = root.joinpath(*relative.split("/"))
        checksum_lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}")
    checksum_lines.append(f"{'0' * 64}  validation/smoke-zh.wav")
    (root / "SHA256SUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    return root


def _create_legacy_root(root: Path) -> Path:
    (root / "configs").mkdir(parents=True)
    (root / ".venv" / "Scripts").mkdir(parents=True)
    for relative in (
        "start.bat",
        "api.py",
        "configs/tts_infer.yaml",
        ".venv/Scripts/python.exe",
    ):
        root.joinpath(*relative.split("/")).write_text("fixture", encoding="utf-8")
    return root


class VoicePackageManagerTests(unittest.TestCase):
    def test_release_urls_use_modelscope_and_huggingface_single_archive(self):
        urls = get_voice_package_urls()
        self.assertEqual(len(urls), 2)
        self.assertIn("modelscope.cn", urls[0])
        self.assertIn("huggingface.co", urls[1])
        self.assertTrue(urls[0].endswith("Aemeath_ONNX_GSV_Medium_FP16.rar"))

    def test_package_hash_validation_allows_omitted_optional_smoke_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root = _create_fake_package(Path(tmp) / "ONNX_aimisiV2")
            validation = validate_voice_package(package_root, verify_hashes=True)

        self.assertTrue(validation.valid, validation.reason)
        self.assertEqual(validation.manifest["name"], "aimisiV2")

    def test_package_validation_accepts_full_precision_g2pw_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root = _create_fake_package(
                Path(tmp) / "ONNX_aimisiV2",
                g2pw_model_name="g2pW.onnx",
            )
            validation = validate_voice_package(package_root, verify_hashes=True)

        self.assertTrue(validation.valid, validation.reason)

    def test_package_validation_rejects_missing_g2pw_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root = _create_fake_package(
                Path(tmp) / "ONNX_aimisiV2",
                g2pw_model_name=None,
            )
            validation = validate_voice_package(package_root)

        self.assertFalse(validation.valid)
        self.assertIn("G2PW ONNX 模型", validation.reason)

    def test_package_validation_requires_gpt_sovits_pronunciation_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root = _create_fake_package(Path(tmp) / "ONNX_aimisiV2")
            (package_root / "common" / "G2P" / "G2PW" / "polyphonic-fix.rep").unlink()
            validation = validate_voice_package(package_root)

        self.assertFalse(validation.valid)
        self.assertIn("polyphonic-fix.rep", validation.reason)

    def test_g2pw_model_must_be_covered_by_checksum_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root = _create_fake_package(Path(tmp) / "ONNX_aimisiV2")
            checksum_path = package_root / "SHA256SUMS.txt"
            checksum_lines = [
                line
                for line in checksum_path.read_text(encoding="utf-8").splitlines()
                if "g2pW_full_precision.onnx" not in line
            ]
            checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
            validation = validate_voice_package(package_root, verify_hashes=True)

        self.assertFalse(validation.valid)
        self.assertIn("g2pW_full_precision.onnx", validation.reason)

    def test_package_hash_validation_rejects_changed_required_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root = _create_fake_package(Path(tmp) / "ONNX_aimisiV2")
            (package_root / "reference.txt").write_text("changed", encoding="utf-8")
            validation = validate_voice_package(package_root, verify_hashes=True)

        self.assertFalse(validation.valid)
        self.assertIn("reference.txt", validation.reason)

    def test_format_two_package_without_current_runtime_revision_requires_update(self):
        for revision in (None, 1, "invalid"):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as tmp:
                package_root = _create_fake_package(Path(tmp) / "ONNX_aimisiV2")
                manifest_path = package_root / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if revision is None:
                    manifest.pop("runtime_revision")
                else:
                    manifest["runtime_revision"] = revision
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

                validation = validate_voice_package(package_root)

            self.assertFalse(validation.valid)
            self.assertEqual(validation.reason, "语音包版本过旧，请安装最新语音包")

    def test_old_runtime_revision_is_reported_as_install_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root = _create_fake_package(Path(tmp) / "ONNX_aimisiV2")
            manifest_path = package_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["runtime_revision"] = VOICE_PACKAGE_RUNTIME_REVISION - 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with patch.object(
                manager, "_standard_package_candidates", return_value=(package_root,)
            ), patch.object(
                manager, "get_gsvmove_launcher_path", return_value=Path(tmp) / "missing.bat"
            ):
                status = manager.get_voice_package_status()

        self.assertEqual(status.kind, "invalid")
        self.assertTrue(status.install_required)
        self.assertEqual(status.reason, "语音包版本过旧，请安装最新语音包")

    def test_archive_member_guard_rejects_parent_and_drive_paths(self):
        for value in ("../escape.bin", "C:/escape.bin", "/escape.bin"):
            with self.subTest(value=value), self.assertRaises(VoicePackageError):
                manager._safe_relative_path(value)

    def test_invalid_status_exposes_package_root_for_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            package_root = base / manager.VOICE_PACKAGE_DIR_NAME
            package_root.mkdir()
            launcher = base / "missing-launcher.bat"
            with patch.object(
                manager, "_standard_package_candidates", return_value=(package_root,)
            ), patch.object(manager, "get_gsvmove_launcher_path", return_value=launcher):
                status = manager.get_voice_package_status()

        self.assertEqual(status.kind, "invalid")
        self.assertEqual(status.package_root, package_root)

    def test_remove_voice_package_deletes_managed_root_and_matching_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            drive = base / "drive"
            drive.mkdir()
            package_root = _create_fake_package(
                drive / "AemeathDeskPet" / "voice" / manager.VOICE_PACKAGE_DIR_NAME
            )
            state_path = base / "state" / "voice_package.json"
            manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
            with patch.object(manager, "list_fixed_drive_roots", return_value=(drive,)), patch.object(
                manager, "get_voice_package_state_path", return_value=state_path
            ):
                manager._write_install_state(package_root, manifest)
                removed = remove_voice_package(package_root)

            self.assertEqual(removed, package_root)
            self.assertFalse(package_root.exists())
            self.assertFalse(state_path.exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction/symlink path behavior")
    def test_remove_voice_package_clears_state_saved_through_resolved_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            physical_root = base / "physical"
            physical_root.mkdir()
            logical_root = base / "logical"
            try:
                logical_root.symlink_to(physical_root, target_is_directory=True)
            except OSError:
                junction = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(logical_root), str(physical_root)],
                    capture_output=True,
                    check=False,
                )
                if junction.returncode != 0:
                    detail = junction.stderr.decode(errors="replace").strip()
                    self.skipTest(f"directory link unavailable: {detail}")

            drive = logical_root / "drive"
            drive.mkdir()
            package_root = _create_fake_package(
                drive / "AemeathDeskPet" / "voice" / manager.VOICE_PACKAGE_DIR_NAME
            )
            state_path = base / "state" / "voice_package.json"
            manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))

            with patch.object(manager, "list_fixed_drive_roots", return_value=(drive,)), patch.object(
                manager, "get_voice_package_state_path", return_value=state_path
            ):
                manager._write_install_state(package_root, manifest)
                stored_root = json.loads(state_path.read_text(encoding="utf-8"))["package_root"]
                self.assertEqual(manager._path_key(Path(stored_root)), manager._path_key(package_root.resolve()))
                remove_voice_package(package_root)

            self.assertFalse(package_root.exists())
            self.assertFalse(state_path.exists())

    def test_remove_voice_package_allows_corrupted_managed_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp) / "drive"
            package_root = drive / "AemeathDeskPet" / "voice" / manager.VOICE_PACKAGE_DIR_NAME
            package_root.mkdir(parents=True)
            (package_root / "broken.bin").write_bytes(b"broken")
            with patch.object(manager, "list_fixed_drive_roots", return_value=(drive,)), patch.object(
                manager, "get_voice_package_state_path", return_value=Path(tmp) / "missing-state.json"
            ):
                remove_voice_package(package_root)

            self.assertFalse(package_root.exists())

    def test_remove_voice_package_rejects_unmanaged_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            managed_drive = base / "managed"
            managed_drive.mkdir()
            package_root = base / "elsewhere" / manager.VOICE_PACKAGE_DIR_NAME
            package_root.mkdir(parents=True)
            with patch.object(
                manager, "list_fixed_drive_roots", return_value=(managed_drive,)
            ), self.assertRaises(VoicePackageError):
                remove_voice_package(package_root)

            self.assertTrue(package_root.exists())

    def test_service_releases_engine_before_removing_package(self):
        events = []
        service = service_module.GsvmoveService.__new__(service_module.GsvmoveService)
        service._infer_lock = threading.RLock()
        service.prepare_voice_package_install = Mock(side_effect=lambda: events.append("prepare"))
        package_root = Path("D:/AemeathDeskPet/voice/ONNX_aimisiV2")

        with patch.object(
            service_module,
            "remove_voice_package_files",
            side_effect=lambda root: events.append(("remove", root)) or root,
        ):
            result = service.remove_voice_package(package_root)

        self.assertEqual(result, package_root)
        self.assertEqual(events, ["prepare", ("remove", package_root)])

    def test_prestart_model_load_holds_inference_lock(self):
        events = []

        class RecordingLock:
            def __enter__(self):
                events.append("lock-enter")

            def __exit__(self, *_args):
                events.append("lock-exit")

        service = service_module.GsvmoveService.__new__(service_module.GsvmoveService)
        service._infer_lock = RecordingLock()
        service._ensure_runtime_ready = Mock(
            side_effect=lambda: events.append("load") or True
        )
        service._warmup_service_once = Mock(side_effect=lambda: events.append("warmup"))

        service._prestart_worker()

        self.assertEqual(events, ["lock-enter", "load", "warmup", "lock-exit"])

    def test_legacy_cleanup_deletes_only_valid_isolated_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            shared = base / "shared"
            shared.mkdir()
            launcher = shared / "start_gsvmove.bat"
            launcher.write_text("@echo off", encoding="utf-8")
            root_file = shared / "config" / "gsvmove_root.txt"
            root_file.parent.mkdir()
            root_file.write_text("legacy", encoding="utf-8")
            legacy = _create_legacy_root(base / "legacy" / "GSVmove")
            active = _create_fake_package(base / "voice" / "ONNX_aimisiV2")

            with patch.object(manager, "get_shared_root_dir", return_value=shared), patch.object(
                manager, "get_gsvmove_launcher_path", return_value=launcher
            ):
                warnings = remove_legacy_gsvmove_runtime(legacy, active, root_file)

            self.assertEqual(warnings, ())
            self.assertFalse(legacy.exists())
            self.assertFalse(launcher.exists())
            self.assertFalse(root_file.exists())

    def test_activation_rolls_back_old_package_when_state_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "target"
            source.mkdir()
            target.mkdir()
            (source / "marker.txt").write_text("new", encoding="utf-8")
            (target / "marker.txt").write_text("old", encoding="utf-8")

            with patch.object(manager, "_write_install_state", side_effect=OSError("state failed")):
                with self.assertRaises(OSError):
                    VoicePackageInstaller._activate_package(source, target, {})

            self.assertEqual((target / "marker.txt").read_text(encoding="utf-8"), "old")

    def test_official_unrar_backend_is_shipped_and_verified(self):
        path = ensure_bundled_unrar()
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "UnRAR.exe")
        self.assertTrue((path.parent / "LICENSE-UnRAR.txt").is_file())

    def test_single_archive_download_uses_selected_profile(self):
        progress = []
        installer = VoicePackageInstaller(progress_callback=lambda *values: progress.append(values))
        profile = manager.get_voice_package_profile("int8")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield b"Rar!\x1a\x07\x01\x00fixture"

        class FakeSession:
            def get(self, *_args, **_kwargs):
                return FakeResponse()

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            manager.requests, "Session", side_effect=FakeSession
        ):
            archive = installer._download_mirror(
                "Test", "https://example.test/archive.rar", profile, Path(tmp)
            )
            self.assertTrue(archive.is_file())

        self.assertEqual(archive.name, profile.archive_name)
        self.assertEqual(progress[-1][2], profile.archive_bytes)

    def test_remote_archive_size_prefers_modelscope_head_response(self):
        calls = []

        class FakeResponse:
            headers = {"Content-Length": str(2 * 1024 ** 3)}

            def raise_for_status(self):
                return None

            def close(self):
                return None

        class FakeSession:
            def head(self, url, **_kwargs):
                calls.append(url)
                return FakeResponse()

            def close(self):
                return None

        with patch.object(manager.requests, "Session", return_value=FakeSession()):
            remote = manager.fetch_voice_package_size("fp16")

        self.assertIsNotNone(remote)
        self.assertEqual(remote.archive_bytes, 2 * 1024 ** 3)
        self.assertEqual(remote.source_name, "ModelScope")
        self.assertEqual(len(calls), 1)
        self.assertIn("modelscope.cn", calls[0])

    def test_remote_archive_size_uses_range_when_head_has_no_length(self):
        calls = []

        class FakeResponse:
            def __init__(self, headers):
                self.headers = headers

            def raise_for_status(self):
                return None

            def close(self):
                return None

        class FakeSession:
            def head(self, url, **_kwargs):
                calls.append(("head", url))
                return FakeResponse({})

            def get(self, url, **kwargs):
                calls.append(("get", url, kwargs.get("headers", {})))
                return FakeResponse({"Content-Range": "bytes 0-0/123456789"})

            def close(self):
                return None

        with patch.object(manager.requests, "Session", return_value=FakeSession()):
            remote = manager.fetch_voice_package_size("fp16")

        self.assertIsNotNone(remote)
        self.assertEqual(remote.archive_bytes, 123456789)
        self.assertEqual(calls[0][0], "head")
        self.assertEqual(calls[1][0], "get")
        self.assertEqual(calls[1][2]["Range"], "bytes=0-0")

    def test_remote_archive_size_falls_back_to_huggingface(self):
        calls = []

        class FakeResponse:
            headers = {"Content-Length": "987654321"}

            def raise_for_status(self):
                return None

            def close(self):
                return None

        class FakeSession:
            def head(self, url, **_kwargs):
                calls.append(url)
                if "modelscope.cn" in url:
                    raise RuntimeError("ModelScope unavailable")
                return FakeResponse()

            def close(self):
                return None

        with patch.object(manager.requests, "Session", return_value=FakeSession()):
            remote = manager.fetch_voice_package_size("fp16")

        self.assertIsNotNone(remote)
        self.assertEqual(remote.archive_bytes, 987654321)
        self.assertEqual(remote.source_name, "Hugging Face")
        self.assertEqual(len(calls), 2)

    def test_download_progress_uses_response_content_length(self):
        progress = []
        installer = VoicePackageInstaller(progress_callback=lambda *values: progress.append(values))
        profile = manager.get_voice_package_profile("int8")

        class FakeResponse:
            headers = {"Content-Length": "123456"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield b"Rar!\x1a\x07\x01\x00fixture"

        class FakeSession:
            def get(self, *_args, **_kwargs):
                return FakeResponse()

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            manager.requests, "Session", side_effect=FakeSession
        ):
            installer._download_mirror(
                "Test", "https://example.test/archive.rar", profile, Path(tmp)
            )

        self.assertEqual(progress[-1][2], 123456)

    def test_backend_preparation_overlaps_parallel_download_start(self):
        installer = VoicePackageInstaller()
        info_messages = []
        installer._info_callback = info_messages.append
        download_started = threading.Event()
        backend_finished = threading.Event()
        profile = manager.get_voice_package_profile("fp16")
        expected_archive = Path("voice-package.rar")

        def download(_download_dir, received_profile):
            self.assertEqual(received_profile, profile)
            download_started.set()
            if not backend_finished.wait(2.0):
                raise AssertionError("backend and download did not overlap")
            return "Test", expected_archive

        def prepare_backend():
            if not download_started.wait(2.0):
                raise AssertionError("download did not start during backend preparation")
            backend_finished.set()
            return Path("UnRAR.exe")

        with patch.object(installer, "_download_parts", side_effect=download), patch.object(
            manager, "ensure_bundled_unrar", side_effect=prepare_backend
        ):
            unrar_path, source, archive = installer._prepare_backend_and_download(
                Path("downloads"), profile
            )

        self.assertEqual(unrar_path, Path("UnRAR.exe"))
        self.assertEqual(source, "Test")
        self.assertEqual(archive, expected_archive)
        self.assertTrue(any("RAR 解压后端已就绪" in message for message in info_messages))

    def test_extract_completion_stops_at_install_stage_boundary(self):
        progress = []
        installer = VoicePackageInstaller(
            progress_callback=lambda *values: progress.append(values)
        )
        process = Mock(returncode=0)
        process.poll.return_value = 0

        with patch.object(manager, "_list_archive_members", return_value=("manifest.json",)), patch.object(
            manager, "_extract_command", return_value=["UnRAR.exe"]
        ), patch.object(manager.subprocess, "Popen", return_value=process):
            installer._extract(Path("UnRAR.exe"), Path("part01.rar"), Path("extract"))

        self.assertEqual(
            progress[-1],
            (
                "extract",
                manager._INSTALL_EXTRACT_END,
                manager._INSTALL_PROGRESS_TOTAL,
                "语音包解压完成，正在校验",
            ),
        )
        self.assertLess(progress[-1][1], progress[-1][2])

    def test_extract_keeps_progress_indeterminate_without_scanning_output_tree(self):
        progress = []
        installer = VoicePackageInstaller(
            progress_callback=lambda *values: progress.append(values)
        )
        process = Mock(returncode=0)
        process.poll.side_effect = (None, None, 0)

        with patch.object(manager, "_list_archive_members", return_value=("manifest.json",)), patch.object(
            manager, "_extract_command", return_value=["UnRAR.exe"]
        ), patch.object(manager.subprocess, "Popen", return_value=process), patch.object(
            manager.os, "walk"
        ) as walk, patch.object(manager.time, "sleep"):
            installer._extract(Path("UnRAR.exe"), Path("voice-package.rar"), Path("extract"))

        walk.assert_not_called()
        self.assertEqual(
            progress[0],
            ("extract", 0, 0, "正在解压角色模型与公共模型"),
        )
        self.assertEqual(
            progress[-1],
            (
                "extract",
                manager._INSTALL_EXTRACT_END,
                manager._INSTALL_PROGRESS_TOTAL,
                "语音包解压完成，正在校验",
            ),
        )

    def test_install_downloads_before_recorded_only_legacy_cleanup(self):
        events = []
        messages = []
        progress = []
        installer = VoicePackageInstaller(
            info_callback=messages.append,
            progress_callback=lambda *values: progress.append(values),
        )
        manifest = {
            "format": VOICE_PACKAGE_FORMAT,
            "format_version": VOICE_PACKAGE_FORMAT_VERSION,
            "runtime_revision": VOICE_PACKAGE_RUNTIME_REVISION,
            "name": "aimisiV2",
            "precision_profile": "fp16",
        }

        def prepare(_download_dir, _profile):
            events.append("download")
            return Path("UnRAR.exe"), "Test", Path("voice-package.rar")

        def activate(_source, _target, _manifest):
            events.append("activate")

        def resolve(*_args, **kwargs):
            events.append("resolve")
            self.assertFalse(kwargs["scan_search_bases"])
            return None, None

        def cleanup(*_args):
            events.append("cleanup")
            return ()

        def validate(_package_root, *, verify_hashes, progress_callback):
            self.assertTrue(verify_hashes)
            self.assertIsNotNone(progress_callback)
            progress_callback(1, 2)
            progress_callback(2, 2)
            return manager.VoicePackageValidation(True, "ok", manifest)

        def extract(*_args):
            installer._report(
                "extract",
                manager._INSTALL_EXTRACT_END,
                manager._INSTALL_PROGRESS_TOTAL,
                "extract done",
            )

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            installer, "_prepare_backend_and_download", side_effect=prepare
        ), patch.object(installer, "_extract", side_effect=extract), patch.object(
            installer, "_locate_extracted_package", return_value=Path(tmp) / "package"
        ), patch.object(
            manager,
            "validate_voice_package",
            side_effect=validate,
        ), patch.object(installer, "_ensure_runtime_dependencies"), patch.object(
            installer, "_activate_package", side_effect=activate
        ), patch.object(
            manager, "resolve_legacy_gsvmove_root", side_effect=resolve
        ) as resolve_mock, patch.object(
            manager, "remove_legacy_gsvmove_runtime", side_effect=cleanup
        ):
            result = installer.install(Path(tmp))

        self.assertEqual(result.source_name, "Test")
        self.assertEqual(events, ["download", "activate", "resolve", "cleanup"])
        resolve_mock.assert_called_once_with(scan_search_bases=False)
        self.assertIn("磁盘空间检查完成，正在创建安装目录", messages)
        self.assertIn("新语音包已激活，正在清理已记录的旧运行时", messages)
        install_progress = [item for item in progress if item[0] == "extract"]
        install_values = [item[1] for item in install_progress]
        self.assertEqual(install_values, sorted(install_values))
        self.assertTrue(all(item[2] == manager._INSTALL_PROGRESS_TOTAL for item in install_progress))
        self.assertTrue(
            any(
                manager._INSTALL_EXTRACT_END < item[1] < manager._INSTALL_VERIFY_END
                for item in install_progress
            )
        )
        self.assertEqual(install_values[-1], manager._INSTALL_PROGRESS_TOTAL)

    def test_cancel_closes_active_download_sessions(self):
        installer = VoicePackageInstaller()
        token = object()
        session = Mock()
        installer._register_download_session(token, session)

        installer.cancel()

        session.close.assert_called_once_with()
        self.assertTrue(installer._cancel_event.is_set())
        self.assertEqual(installer._active_sessions, {})

    def test_dependency_install_can_be_cancelled(self):
        installer = VoicePackageInstaller()
        process_started = threading.Event()

        class FakeProcess:
            returncode = None
            pid = 42

            def poll(self):
                process_started.set()
                return self.returncode

        fake_process = FakeProcess()

        def terminate(_process):
            fake_process.returncode = -1

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            manager, "missing_runtime_modules", return_value=("onnxruntime",)
        ), patch.object(manager.subprocess, "Popen", return_value=fake_process), patch.object(
            manager, "_terminate_process", side_effect=terminate
        ), patch.object(
            manager.time, "sleep", side_effect=lambda _seconds: installer.cancel()
        ):
            with self.assertRaises(VoicePackageCancelled):
                installer._ensure_runtime_dependencies(Path(tmp))

        self.assertTrue(process_started.is_set())
        self.assertIsNone(installer._active_process)


if __name__ == "__main__":
    unittest.main()
