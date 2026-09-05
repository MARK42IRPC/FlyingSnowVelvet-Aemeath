import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from config import voice_runtime
from lib.script.gsvmove import service as service_module
from lib.script.gsvmove.hybrid_worker import (
    CpuVoiceWorkerRuntime,
    HybridVoiceWorkerRuntime,
    VoiceWorkerRuntime,
    _get_cuda_nvidia_bin_dirs,
    _get_cuda_bundle_bin_dir,
    _preload_onnxruntime_dlls,
    _resolve_worker_output,
    _terminate_worker_process_tree,
)
from lib.script.gsvmove.package_manager import VoicePackageStatus
from lib.core.event.center import Event, EventType


class DirectMLHybridWorkerTests(unittest.TestCase):
    def test_cuda_preload_calls_onnxruntime_dll_loader(self):
        runtime_module = Mock()

        _preload_onnxruntime_dlls("cuda", runtime_module)

        runtime_module.preload_dlls.assert_called_once_with()

    def test_cuda_bundle_preload_uses_marker_dll_directory(self):
        runtime_module = Mock()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            python_path = root / "Scripts" / "python.exe"
            dll_dir = root / "Lib" / "site-packages" / "aemeath_cuda_runtime" / "cuda" / "bin"
            dll_dir.mkdir(parents=True)
            (root / "runtime.json").write_text(
                json.dumps(
                    {
                        "source": "bundle",
                        "dll_directory": "Lib/site-packages/aemeath_cuda_runtime/cuda/bin",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(_get_cuda_bundle_bin_dir(python_path), dll_dir)
            _preload_onnxruntime_dlls(
                "cuda",
                runtime_module,
                dll_directory=dll_dir,
            )
        runtime_module.preload_dlls.assert_called_once_with(directory=str(dll_dir))

    def test_cuda_worker_discovers_nvidia_bin_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            python_path = root / "Scripts" / "python.exe"
            for name in ("cublas", "cudnn"):
                (root / "Lib" / "site-packages" / "nvidia" / name / "bin").mkdir(
                    parents=True
                )

            directories = _get_cuda_nvidia_bin_dirs(python_path)

        self.assertEqual(
            directories,
            (
                root / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
                root / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
            ),
        )

    def test_windows_timeout_terminates_full_worker_process_tree(self):
        process = Mock(pid=321)
        process.poll.return_value = None
        with patch("lib.script.gsvmove.hybrid_worker.os.name", "nt"), patch(
            "lib.script.gsvmove.hybrid_worker.subprocess.run"
        ) as run:
            _terminate_worker_process_tree(process)

        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "321", "/T", "/F"])
        process.wait.assert_called_once_with(timeout=3.0)
        process.terminate.assert_not_called()

    def test_cpu_runtime_uses_low_priority_isolated_worker(self):
        process = Mock()
        process.poll.return_value = 0
        process.stdin = Mock()
        process.stdout = Mock()
        process.stderr = Mock()
        worker_thread = Mock()
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            VoiceWorkerRuntime,
            "_next_message",
            return_value={"type": "ready", "provider": "cpu"},
        ), patch("lib.script.gsvmove.hybrid_worker.subprocess.Popen", return_value=process) as popen, patch(
            "lib.script.gsvmove.hybrid_worker.threading.Thread",
            return_value=worker_thread,
        ):
            runtime = CpuVoiceWorkerRuntime(Path(tmpdir) / "package", Path(tmpdir) / "output")
            runtime.close()

        command = popen.call_args.args[0]
        flags = popen.call_args.kwargs["creationflags"]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[command.index("--provider") + 1], "cpu")
        self.assertEqual(
            flags & getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
            getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
        )
        self.assertIn("-I", command)
        self.assertIn("utf8", command)
        self.assertEqual(popen.call_args.kwargs["env"]["PYTHONNOUSERSITE"], "1")
        self.assertNotIn("PYTHONPATH", popen.call_args.kwargs["env"])

    def test_bundled_directml_runtime_marker_is_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            app_root = Path(tmpdir) / "install" / "app"
            runtime_root = voice_runtime.get_bundled_directml_runtime_root(app_root)
            dll = voice_runtime.get_bundled_directml_dll_path(app_root)
            python = voice_runtime.get_bundled_python_path(app_root)
            dll.parent.mkdir(parents=True)
            python.parent.mkdir(parents=True)
            dll.write_bytes(b"DirectML")
            python.write_bytes(b"python")
            (runtime_root / voice_runtime.DIRECTML_RUNTIME_MARKER_NAME).write_text(
                json.dumps(
                    {
                        "format": voice_runtime.DIRECTML_BUNDLED_FORMAT,
                        "format_version": voice_runtime.DIRECTML_BUNDLED_FORMAT_VERSION,
                        "runtime": "onnxruntime-directml",
                        "version": voice_runtime.DIRECTML_RUNTIME_VERSION,
                        "abi": voice_runtime.DIRECTML_RUNTIME_ABI,
                        "provider": "DmlExecutionProvider",
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(voice_runtime.is_bundled_directml_runtime_ready(app_root))

    def test_hybrid_worker_passes_bundled_overlay_to_isolated_runtime(self):
        python = Path("C:/release/runtime/python311/python.exe")
        overlay = Path("C:/release/runtime/onnx-directml/site-packages")
        with patch("config.voice_runtime.is_directml_runtime_ready", return_value=True), patch(
            "config.voice_runtime.get_directml_worker_python_path", return_value=python
        ), patch(
            "config.voice_runtime.get_directml_worker_site_packages", return_value=overlay
        ), patch.object(VoiceWorkerRuntime, "__init__", return_value=None) as initialize:
            HybridVoiceWorkerRuntime(Path("package"), Path("output"))

        initialize.assert_called_once_with(
            Path("package"),
            Path("output"),
            provider="hybrid",
            python_path=python,
            isolate_user_site=True,
            module_overlay=overlay,
        )

    def test_cuda_runtime_prepends_nvidia_dll_directories_to_worker_path(self):
        process = Mock()
        process.poll.return_value = 0
        process.stdin = Mock()
        process.stdout = Mock()
        process.stderr = Mock()
        worker_thread = Mock()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            python_path = root / "Scripts" / "python.exe"
            dll_dir = root / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin"
            dll_dir.mkdir(parents=True)
            with patch.object(
                VoiceWorkerRuntime,
                "_next_message",
                return_value={"type": "ready", "provider": "cuda"},
            ), patch(
                "lib.script.gsvmove.hybrid_worker.subprocess.Popen",
                return_value=process,
            ) as popen, patch(
                "lib.script.gsvmove.hybrid_worker.threading.Thread",
                return_value=worker_thread,
            ):
                runtime = VoiceWorkerRuntime(
                    root / "package",
                    root / "output",
                    provider="cuda",
                    python_path=python_path,
                )
                runtime.close()

        path_value = popen.call_args.kwargs["env"]["PATH"]
        self.assertEqual(path_value.split(os.pathsep)[0], str(dll_dir))

    def test_runtime_path_is_versioned_under_shared_voice_root(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ", {"AEMEATH_DESK_PET_HOME": tmpdir}
        ):
            root = voice_runtime.get_directml_runtime_root()

        self.assertEqual(
            root,
            Path(tmpdir)
            / "voice"
            / "runtimes"
            / "onnx-directml"
            / "1.22.0-cp311-win_amd64",
        )

    def test_worker_output_cannot_escape_managed_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            destination = _resolve_worker_output(root, "voice.wav")
            escaped = _resolve_worker_output(root, "../outside.wav")

        self.assertEqual(destination, root / "voice.wav")
        self.assertEqual(escaped, root / "outside.wav")

    def test_hybrid_start_failure_falls_back_to_cpu_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status = VoicePackageStatus("installed", "ok", root)
            cpu_runtime = Mock()
            service = object.__new__(service_module.GsvmoveService)
            service._engine_lock = threading.RLock()
            service._engine = None
            service._engine_package_root = None
            service._engine_backend = None
            service._engine_requested_backend = None
            service._output_dir = root / "output"

            with patch.object(service_module, "get_voice_package_status", return_value=status), patch.object(
                service_module, "_is_gsv_gpu_hybrid_enabled", return_value=True
            ), patch.object(
                service_module, "_is_gsv_nvidia_cuda_acceleration_enabled", return_value=False
            ), patch.object(
                service_module, "HybridVoiceWorkerRuntime", side_effect=RuntimeError("DML unavailable")
            ) as hybrid, patch.object(
                service_module, "CpuVoiceWorkerRuntime", return_value=cpu_runtime
            ) as cpu:
                ready = service._ensure_runtime_ready()
                ready_again = service._ensure_runtime_ready()

        self.assertTrue(ready)
        self.assertTrue(ready_again)
        hybrid.assert_called_once()
        cpu.assert_called_once_with(root, root / "output")
        self.assertEqual(service._engine_backend, "cpu-fallback")
        self.assertEqual(service._engine_requested_backend, "hybrid")

    def test_cuda_start_failure_uses_directml_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status = VoicePackageStatus("installed", "ok", root)
            directml_runtime = Mock()
            service = object.__new__(service_module.GsvmoveService)
            service._engine_lock = threading.RLock()
            service._engine = None
            service._engine_package_root = None
            service._engine_backend = None
            service._engine_requested_backend = None
            service._output_dir = root / "output"

            with patch.object(service_module, "get_voice_package_status", return_value=status), patch.object(
                service_module, "_is_gsv_nvidia_cuda_acceleration_enabled", return_value=True
            ), patch.object(
                service_module, "CudaVoiceWorkerRuntime", side_effect=RuntimeError("CUDA unavailable")
            ) as cuda, patch.object(
                service_module, "HybridVoiceWorkerRuntime", return_value=directml_runtime
            ) as directml:
                ready = service._ensure_runtime_ready()
                ready_again = service._ensure_runtime_ready()

        self.assertTrue(ready)
        self.assertTrue(ready_again)
        cuda.assert_called_once_with(root, root / "output")
        directml.assert_called_once_with(root, root / "output")
        self.assertEqual(service._engine_backend, "directml-fallback")
        self.assertEqual(service._engine_requested_backend, "cuda")

    def test_config_switch_schedules_active_hybrid_worker_release(self):
        service = object.__new__(service_module.GsvmoveService)
        service._engine_lock = threading.RLock()
        service._engine = Mock()
        service._engine_requested_backend = "hybrid"

        compute_hub = Mock()
        with patch.object(service_module, "get_compute_hub", return_value=compute_hub):
            service._on_config_updated(Event(EventType.CONFIG_UPDATED, {
                "source": "ai",
                "values": {"gsv_gpu_hybrid": False},
            }))

        compute_hub.submit_latest.assert_called_once_with(
            "gsvmove_backend_switch",
            service._switch_backend_after_config,
            executor="vector",
        )

    def test_automatic_warmup_starts_only_after_app_main(self):
        service = object.__new__(service_module.GsvmoveService)
        service.kickoff_prestart = Mock()

        service._on_app_main(Event(EventType.APP_MAIN, {}))

        service.kickoff_prestart.assert_called_once_with()

    def test_prestart_submission_does_not_probe_package_on_main_thread(self):
        service = object.__new__(service_module.GsvmoveService)
        service._prestart_lock = threading.Lock()
        service._prestart_started = False
        service.auto_start_enabled = Mock(return_value=True)
        compute_hub = Mock()

        with patch.object(service_module, "get_compute_hub", return_value=compute_hub), patch.object(
            service_module, "get_voice_package_status"
        ) as status:
            service.kickoff_prestart()

        status.assert_not_called()
        compute_hub.submit_latest.assert_called_once_with(
            "gsvmove_prestart",
            service._prestart_worker,
            executor="vector",
        )

    def test_backend_switch_closes_active_worker(self):
        service = object.__new__(service_module.GsvmoveService)
        service._infer_lock = threading.RLock()
        service._engine_lock = threading.RLock()
        service._prestart_lock = threading.Lock()
        service._prestart_started = True
        service._warmup_done = True
        worker = Mock()
        service._engine = worker
        service._engine_package_root = Path("voice-package")
        service._engine_backend = "hybrid"
        service._engine_requested_backend = "hybrid"
        service.auto_start_enabled = Mock(return_value=False)

        service._switch_backend_after_config()

        worker.close.assert_called_once_with()
        self.assertIsNone(service._engine)
        self.assertIsNone(service._engine_backend)
        self.assertFalse(service._warmup_done)
        self.assertFalse(service._prestart_started)

    def test_failed_hybrid_request_retries_same_request_on_cpu(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            hybrid = Mock()
            hybrid.synthesize_to_file.side_effect = RuntimeError("device removed")
            cpu = Mock()

            def write_cpu(_payload, destination):
                destination.write_bytes(b"RIFF" + b"0" * 64)
                return destination

            cpu.synthesize_to_file.side_effect = write_cpu
            service = object.__new__(service_module.GsvmoveService)
            service._engine_lock = threading.RLock()
            service._engine = hybrid
            service._engine_package_root = root
            service._engine_backend = "hybrid"
            service._engine_requested_backend = "hybrid"
            service._output_dir = root / "output"
            service._output_dir.mkdir()
            service._ensure_runtime_ready = Mock(return_value=True)

            def activate(_package_root):
                service._engine = cpu
                service._engine_backend = "cpu-fallback"
                return True

            service._activate_cpu_fallback_locked = Mock(side_effect=activate)
            output = service._synthesize_to_file({"text": "hello", "save_audio_cache": False})

        self.assertIsNotNone(output)
        hybrid.synthesize_to_file.assert_called_once()
        cpu.synthesize_to_file.assert_called_once()
        service._activate_cpu_fallback_locked.assert_called_once_with(root)


if __name__ == "__main__":
    unittest.main()
