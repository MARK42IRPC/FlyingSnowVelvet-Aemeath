import contextlib
import hashlib
import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install_deps
from lib.core import dsh_runtime_contract as dsh_config
from lib.script.gsvmove import rar_backend


class InstallDependenciesProgressTests(unittest.TestCase):
    def test_dsh_install_prompt_supports_noninteractive_override(self):
        with patch.dict(install_deps.os.environ, {"FLYING_SNOW_INSTALL_DSH": "0"}, clear=False):
            self.assertFalse(install_deps._should_install_dsh())
        with patch.dict(install_deps.os.environ, {"FLYING_SNOW_INSTALL_DSH": "yes"}, clear=False):
            self.assertTrue(install_deps._should_install_dsh())

    def test_dsh_install_prompt_defaults_to_yes(self):
        with patch.dict(install_deps.os.environ, {}, clear=True), patch(
            "builtins.input", return_value=""
        ):
            self.assertTrue(install_deps._should_install_dsh())

    def test_dsh_install_prompt_accepts_explicit_no(self):
        with patch.dict(install_deps.os.environ, {}, clear=True), patch(
            "builtins.input", return_value="n"
        ):
            self.assertFalse(install_deps._should_install_dsh())

    def test_node_urls_are_ordered_by_concurrent_ping_latency(self):
        install_deps._NODE_SOURCE_ORDER = None
        urls = (
            "https://slow.example/node.zip",
            "https://fast.example/node.zip",
            "https://unreachable.example/node.zip",
        )

        def ping(host, **_kwargs):
            return {"slow.example": 80.0, "fast.example": 12.0, "unreachable.example": None}[host]

        try:
            with patch.object(install_deps, "_ping_host_average_ms", side_effect=ping):
                ordered = install_deps._order_node_urls(urls)
            self.assertEqual(ordered, (urls[1], urls[0], urls[2]))
        finally:
            install_deps._NODE_SOURCE_ORDER = None

    def test_uv_managed_base_python_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "uv" / "python" / "cpython-3.11"
            (root / "Lib").mkdir(parents=True)
            python_exe = root / "python.exe"
            python_exe.write_bytes(b"python")
            (root / "Lib" / "EXTERNALLY-MANAGED").write_text(
                "[externally-managed]\n"
                "Error=This Python installation is managed by uv and should not be modified.\n",
                encoding="utf-8",
            )

            self.assertTrue(install_deps._is_uv_managed_python(python_exe))

    def test_uv_created_virtual_environment_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / ".venv"
            scripts = root / "Scripts"
            scripts.mkdir(parents=True)
            python_exe = scripts / "python.exe"
            python_exe.write_bytes(b"python")
            (root / "pyvenv.cfg").write_text(
                "home = C:\\Python311\nuv = 0.11.6\n",
                encoding="utf-8",
            )

            self.assertTrue(install_deps._is_uv_managed_python(python_exe))

    def test_non_uv_python_is_not_excluded_by_external_management_alone(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "python"
            (root / "Lib").mkdir(parents=True)
            python_exe = root / "python.exe"
            python_exe.write_bytes(b"python")
            (root / "Lib" / "EXTERNALLY-MANAGED").write_text(
                "[externally-managed]\nError=Managed by the system package manager.\n",
                encoding="utf-8",
            )

            self.assertFalse(install_deps._is_uv_managed_python(python_exe))

    def test_python_selection_skips_uv_candidate_before_probe(self):
        uv_python = r"C:\Users\test\AppData\Roaming\uv\python\cpython-3.11\python.exe"
        regular_python = r"C:\Python311\python.exe"

        def is_uv_managed(path):
            return str(path) == uv_python

        with patch.object(
            install_deps,
            "_discover_all_pythons",
            return_value=[uv_python, regular_python],
        ), patch.object(
            install_deps,
            "_is_uv_managed_python",
            side_effect=is_uv_managed,
        ), patch.object(
            install_deps,
            "_probe_python_info",
            return_value=(regular_python, (3, 11, 6)),
        ) as probe, patch.object(
            install_deps,
            "_has_pip",
            return_value=True,
        ), patch.object(
            install_deps,
            "_current_runtime_executable",
            return_value=regular_python,
        ):
            selected = install_deps.select_best_python()

        self.assertEqual(selected, (regular_python, True))
        probe.assert_called_once_with(regular_python)

    def test_batch_bootstrap_filters_uv_python_before_launch_probe(self):
        content = (install_deps.PROJECT_ROOT / "安装依赖.bat").read_text(
            encoding="utf-8"
        )

        self.assertIn("function Test-UvManagedPython", content)
        uv_filter = content.index("if(Test-UvManagedPython $resolved){return}")
        launch_probe = content.index("$info=Get-PythonInfo $resolved;")
        self.assertLess(uv_filter, launch_probe)
        self.assertIn("if(Test-UvManagedPython $info.Executable){return}", content)

    def test_dsh_npm_install_uses_fixed_production_lockfile_command(self):
        with patch.object(
            install_deps,
            "_run_command_with_progress",
            return_value=(0, ""),
        ) as run:
            installed, detail = install_deps._run_dsh_npm_ci()

        self.assertTrue(installed)
        self.assertEqual(detail, "")
        command = run.call_args.args[0]
        self.assertEqual(command[2:], [
            "ci",
            "--omit=dev",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ])
        self.assertEqual(
            run.call_args.kwargs["cwd"],
            install_deps.dsh_config.dsh_runtime_root(install_deps.PROJECT_ROOT),
        )
        self.assertEqual(run.call_args.kwargs["kind"], "npm")
        self.assertEqual(run.call_args.kwargs["timeout"], install_deps.DSH_RUNTIME_INSTALL_TIMEOUT)

    def test_dsh_install_repairs_node_modules_without_downloading_node_again(self):
        with patch.object(
            install_deps,
            "_dsh_runtime_ready",
            side_effect=[(False, "DSH 依赖不完整"), (True, "")],
        ), patch.object(
            install_deps.dsh_config,
            "runtime_source_error",
            return_value="",
        ), patch.object(
            install_deps,
            "_node_tree_ready",
            return_value=(True, ""),
        ), patch.object(
            install_deps,
            "_run_dsh_npm_ci",
            return_value=(True, ""),
        ) as npm_ci, patch.object(install_deps, "_dsh_node_urls") as node_urls:
            installed = install_deps.ensure_dsh_office_runtime()

        self.assertTrue(installed)
        npm_ci.assert_called_once_with()
        node_urls.assert_not_called()

    def test_dsh_install_stops_when_release_source_bundle_is_incomplete(self):
        with patch.object(
            install_deps,
            "_dsh_runtime_ready",
            return_value=(False, "源码不完整"),
        ), patch.object(
            install_deps.dsh_config,
            "runtime_source_error",
            return_value="DSH 办公运行时源码不完整：缺少 bridge/index.mjs",
        ), patch.object(install_deps, "_run_dsh_npm_ci") as npm_ci, patch.object(
            install_deps,
            "_dsh_node_urls",
        ) as node_urls:
            installed = install_deps.ensure_dsh_office_runtime()

        self.assertFalse(installed)
        npm_ci.assert_not_called()
        node_urls.assert_not_called()

    def test_node_archive_hash_matches_the_official_release_manifest(self):
        self.assertEqual(
            dsh_config.NODE_ARCHIVE_SHA256,
            "ca2742695be8de44027d71b3f53a4bdb36009b95575fe1ae6f7f0b5ce091cb88",
        )

    def test_directml_runtime_is_installed_in_versioned_venv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "voice" / "runtimes" / "onnx-directml" / "runtime"
            target_python = target / "Scripts" / "python.exe"

            def run(command, timeout=12):
                if command[1:3] == ["-c", "import struct; print(struct.calcsize('P') * 8)"]:
                    return subprocess.CompletedProcess(command, 0, stdout="64\n")
                if "venv" in command:
                    staging = Path(command[-1])
                    (staging / "Scripts").mkdir(parents=True)
                    (staging / "Scripts" / "python.exe").write_bytes(b"python")
                    return subprocess.CompletedProcess(command, 0, stdout="")
                if "pip" in command:
                    return subprocess.CompletedProcess(command, 0, stdout="installed")
                raise AssertionError(command)

            with patch.object(install_deps, "_get_version", return_value=(3, 11, 6)), patch.object(
                install_deps, "_run", side_effect=run
            ), patch.object(
                install_deps.directml_config, "get_directml_runtime_root", return_value=target
            ), patch.object(
                install_deps.directml_config, "get_directml_python_path", return_value=target_python
            ), patch.object(
                install_deps.directml_config, "is_directml_runtime_ready", side_effect=[False, True]
            ), patch.object(
                install_deps, "_directml_runtime_probe", return_value=(True, "")
            ):
                installed = install_deps.ensure_directml_hybrid_runtime(
                    "python.exe",
                    [{"name": "PyPI", "url": "https://pypi.org/simple", "host": "pypi.org"}],
                )

            self.assertTrue(installed)
            marker = json.loads(
                (target / install_deps.directml_config.DIRECTML_RUNTIME_MARKER_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(marker["version"], install_deps.directml_config.DIRECTML_RUNTIME_VERSION)
            self.assertEqual(marker["abi"], "cp311-win_amd64")

    def test_cuda_runtime_is_installed_from_ranked_mirror_in_versioned_venv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "voice" / "runtimes" / "onnx-cuda" / "runtime"
            target_python = target / "Scripts" / "python.exe"
            pip_commands = []

            def run(command, timeout=12):
                if command[1:3] == ["-c", "import struct; print(struct.calcsize('P') * 8)"]:
                    return subprocess.CompletedProcess(command, 0, stdout="64\n")
                if "venv" in command:
                    staging = Path(command[-1])
                    (staging / "Scripts").mkdir(parents=True)
                    (staging / "Scripts" / "python.exe").write_bytes(b"python")
                    return subprocess.CompletedProcess(command, 0, stdout="")
                if "pip" in command:
                    pip_commands.append(command)
                    return subprocess.CompletedProcess(command, 0, stdout="installed")
                raise AssertionError(command)

            def run_progress(command, **kwargs):
                pip_commands.append(command)
                self.assertEqual(kwargs["kind"], "pip")
                self.assertEqual(kwargs["timeout"], install_deps.DSH_RUNTIME_INSTALL_TIMEOUT)
                return 0, "installed"

            mirror = {"name": "Tsinghua", "url": "https://mirror.example/simple", "host": "mirror.example"}
            with patch.object(install_deps, "_get_version", return_value=(3, 11, 6)), patch.object(
                install_deps, "_run", side_effect=run
            ), patch.object(
                install_deps, "_run_command_with_progress", side_effect=run_progress
            ), patch.object(
                install_deps.directml_config, "get_cuda_runtime_root", return_value=target
            ), patch.object(
                install_deps.directml_config, "get_cuda_python_path", return_value=target_python
            ), patch.object(
                install_deps.directml_config, "is_cuda_runtime_ready", side_effect=[False, True]
            ), patch.object(
                install_deps, "_cuda_runtime_probe", return_value=(True, "")
            ):
                installed = install_deps.ensure_cuda_voice_runtime("python.exe", [mirror])

            self.assertTrue(installed)
            self.assertEqual(pip_commands[0][pip_commands[0].index("-i") + 1], mirror["url"])
            self.assertIn(install_deps.directml_config.CUDA_RUNTIME_REQUIREMENT, pip_commands[0])
            self.assertIn("nvidia-cuda-nvrtc-cu12", pip_commands[0])
            self.assertIn("nvidia-cudnn-cu12", pip_commands[0])
            marker = json.loads(
                (target / install_deps.directml_config.CUDA_RUNTIME_MARKER_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(marker["provider"], "CUDAExecutionProvider")

    def test_cuda_probe_preloads_dlls_and_reports_provider_loader_diagnostics(self):
        payload = {
            "python": [3, 11],
            "bits": 64,
            "version": install_deps.directml_config.CUDA_RUNTIME_VERSION,
            "providers": ["CPUExecutionProvider"],
        }
        result = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(payload),
            stderr="Failed to load onnxruntime_providers_cuda.dll: DLL load failed",
        )
        with patch.object(install_deps, "_run", return_value=result) as run:
            ready, detail = install_deps._cuda_runtime_probe(Path("python.exe"))

        self.assertFalse(ready)
        self.assertIn("Failed to load onnxruntime_providers_cuda.dll", detail)
        self.assertIn("preload", run.call_args.args[0][-1])

    def test_gpu_runtime_choice_defaults_to_directml_without_nvidia_gpu(self):
        output = io.StringIO()
        with patch.object(install_deps, "_has_nvidia_gpu", return_value=False), patch(
            "builtins.input", side_effect=lambda prompt: (output.write(prompt), "")[1]
        ), patch.object(install_deps, "_COLOR_ENABLED", True), contextlib.redirect_stdout(output):
            self.assertEqual(install_deps.choose_voice_gpu_runtimes(), (False, True))
        text = output.getvalue()
        self.assertIn("\033[92m[推荐]\033[0m", text)
        self.assertIn("请选择 [1-2，默认 2]", text)
        self.assertNotIn("NVIDIA CUDA", text)

    def test_gpu_runtime_choice_ignores_hidden_cuda_selection_without_nvidia_gpu(self):
        output = io.StringIO()
        with patch.object(install_deps, "_has_nvidia_gpu", return_value=False), patch(
            "builtins.input", return_value="4"
        ), contextlib.redirect_stdout(output):
            self.assertEqual(install_deps.choose_voice_gpu_runtimes(), (False, True))
        self.assertNotIn("NVIDIA CUDA", output.getvalue())

    def test_gpu_runtime_choice_recommends_cuda_with_directml_fallback_for_nvidia_gpu(self):
        output = io.StringIO()
        with patch.object(install_deps, "_has_nvidia_gpu", return_value=True), patch(
            "builtins.input", side_effect=lambda prompt: (output.write(prompt), "")[1]
        ), contextlib.redirect_stdout(output):
            self.assertEqual(install_deps.choose_voice_gpu_runtimes(), (True, True))
        self.assertIn("NVIDIA CUDA", output.getvalue())
        self.assertIn("请选择 [1-4，默认 4]", output.getvalue())
        with patch("builtins.input", return_value="4"):
            self.assertEqual(install_deps.choose_voice_gpu_runtimes(), (True, True))

    def test_python_311_is_preferred_over_current_non_target_runtime(self):
        with patch.object(install_deps, "_current_runtime_executable", return_value="C:\\Python312\\python.exe"):
            target = install_deps._sort_key(((3, 11, 6), "C:\\Python311\\python.exe"))
            current = install_deps._sort_key(((3, 12, 1), "C:\\Python312\\python.exe"))

        self.assertLess(target, current)

    def test_jieba_fast_prebuilt_wheel_is_ordered_before_genie_tts(self):
        names = [package for package, _description, _checks in install_deps.DEPENDENCIES]
        self.assertLess(names.index("jieba-fast"), names.index("genie-tts"))

    def test_opencc_dependency_is_available_before_genie_tts(self):
        names = [package for package, _description, _checks in install_deps.DEPENDENCIES]
        self.assertLess(
            names.index("opencc-python-reimplemented"),
            names.index("genie-tts"),
        )
        entry = next(
            item
            for item in install_deps.DEPENDENCIES
            if item[0] == "opencc-python-reimplemented"
        )
        self.assertEqual(entry[2], ("opencc",))

        requirements = (install_deps.PROJECT_ROOT / "requirements.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertIn("--only-binary=opencc-python-reimplemented", requirements)
        self.assertIn("opencc-python-reimplemented>=0.1.7,<1", requirements)

    def test_webrtcvad_wheels_dependency_checks_webrtcvad_module(self):
        entry = next(
            item for item in install_deps.DEPENDENCIES if item[0] == "webrtcvad-wheels"
        )
        self.assertEqual(entry[2], ("webrtcvad",))

    def test_microphone_runtime_requires_webrtcvad(self):
        checks = []

        def installed(_python, package, import_checks=()):
            checks.append((package, import_checks))
            return True

        with patch.object(install_deps, "_pkg_installed", side_effect=installed):
            ready = install_deps._microphone_runtime_ready("python.exe")

        self.assertTrue(ready)
        self.assertEqual(checks, [
            ("sounddevice", ("sounddevice",)),
            ("vosk", ("vosk",)),
            ("webrtcvad-wheels", ("webrtcvad",)),
        ])

    def test_jieba_fast_wheel_uses_resource_mirror_and_hash(self):
        wheel_bytes = b"verified wheel"
        expected_hash = hashlib.sha256(wheel_bytes).hexdigest()
        installed_paths = []

        def download(_url, destination, *, label):
            self.assertEqual(label, "jieba-fast")
            destination.write_bytes(wheel_bytes)

        def install(_python, requirement, progress_callback, *, mirror=None):
            self.assertIsNone(mirror)
            installed_paths.append(requirement)
            self.assertEqual(requirement.read_bytes(), wheel_bytes)
            progress_callback(80)
            return 0, ""

        with patch.object(
            install_deps, "JIEBA_FAST_WHEEL_SHA256", expected_hash
        ), patch.object(
            install_deps, "_get_version", return_value=(3, 11, 6)
        ), patch.object(
            install_deps,
            "_run",
            return_value=subprocess.CompletedProcess([], 0, stdout="64\n"),
        ), patch.object(
            install_deps, "_resource_urls", return_value=("https://example.invalid/wheel",)
        ), patch.object(
            install_deps, "_stream_download_with_progress", side_effect=download
        ), patch.object(
            install_deps, "_run_pip_requirement_with_progress", side_effect=install
        ):
            result, detail = install_deps._install_jieba_fast_wheel(
                "python.exe", lambda _percent: None
            )

        self.assertTrue(result)
        self.assertEqual(detail, "")
        self.assertEqual(len(installed_paths), 1)
        self.assertFalse(installed_paths[0].exists())

    def test_jieba_fast_wheel_hash_mismatch_falls_back_to_next_mirror(self):
        good_bytes = b"good wheel"
        downloads = []

        def download(url, destination, *, label):
            self.assertEqual(label, "jieba-fast")
            downloads.append(url)
            destination.write_bytes(b"bad wheel" if len(downloads) == 1 else good_bytes)

        with patch.object(
            install_deps, "JIEBA_FAST_WHEEL_SHA256", hashlib.sha256(good_bytes).hexdigest()
        ), patch.object(
            install_deps, "_get_version", return_value=(3, 11, 6)
        ), patch.object(
            install_deps,
            "_run",
            return_value=subprocess.CompletedProcess([], 0, stdout="64\n"),
        ), patch.object(
            install_deps,
            "_resource_urls",
            return_value=("https://first.invalid/wheel", "https://second.invalid/wheel"),
        ), patch.object(
            install_deps, "_stream_download_with_progress", side_effect=download
        ), patch.object(
            install_deps,
            "_run_pip_requirement_with_progress",
            return_value=(0, ""),
        ):
            result, detail = install_deps._install_jieba_fast_wheel(
                "python.exe", lambda _percent: None
            )

        self.assertTrue(result)
        self.assertEqual(detail, "")
        self.assertEqual(downloads, ["https://first.invalid/wheel", "https://second.invalid/wheel"])

    def test_jieba_fast_wheel_rejects_non_python_311_runtime(self):
        with patch.object(install_deps, "_get_version", return_value=(3, 12, 1)), patch.object(
            install_deps,
            "_run",
            return_value=subprocess.CompletedProcess([], 0, stdout="64\n"),
        ), patch.object(install_deps, "_resource_urls") as resource_urls:
            result, detail = install_deps._install_jieba_fast_wheel(
                "python.exe", lambda _percent: None
            )

        self.assertFalse(result)
        self.assertIn("仅支持 64 位 Python 3.11", detail)
        resource_urls.assert_not_called()

    def test_genie_dependency_uses_side_effect_free_module_check(self):
        pip_result = subprocess.CompletedProcess([], 0)
        module_result = subprocess.CompletedProcess([], 0)
        with patch.object(install_deps, "_run_pip", return_value=pip_result), patch.object(
            install_deps, "_run", return_value=module_result
        ) as run:
            installed = install_deps._pkg_installed(
                "python.exe",
                "genie-tts",
                import_checks=("spec:genie_tts",),
            )

        self.assertTrue(installed)
        command = run.call_args.args[0]
        self.assertIn("find_spec('genie_tts')", command[-1])
        self.assertNotIn("import genie_tts", command[-1])

    def test_dependency_summary_and_two_progress_rows_replace_per_package_log(self):
        dependencies = [
            ("ExistingPkg", "existing", ("existing_module",)),
            ("MissingPkg", "missing", ("missing_module",)),
        ]

        def installed(_python, package, import_checks=()):
            return package == "ExistingPkg"

        def install_one(_python, package, _mirrors, progress_callback):
            self.assertEqual(package, "MissingPkg")
            progress_callback(40)
            progress_callback(100)
            return True, ""

        output = io.StringIO()
        with patch.object(install_deps, "DEPENDENCIES", dependencies), patch.object(
            install_deps, "_pkg_installed", side_effect=installed
        ), patch.object(install_deps, "_install_one", side_effect=install_one), patch.object(
            install_deps, "_COLOR_ENABLED", False
        ), patch.object(rar_backend, "is_bundled_unrar_ready", return_value=True), contextlib.redirect_stdout(output):
            result = install_deps.install_all("python.exe", [{"name": "test"}])

        text = output.getvalue()
        self.assertTrue(result)
        self.assertIn("已有依赖：ExistingPkg, UnRAR后端", text)
        self.assertIn("未安装依赖：MissingPkg", text)
        self.assertIn("当前依赖", text)
        self.assertIn("整体进度", text)
        self.assertNotIn("- ExistingPkg (existing)", text)

    def test_overall_progress_counts_completed_modules_only(self):
        dependencies = [
            ("FirstPkg", "first", ()),
            ("SecondPkg", "second", ()),
        ]

        def install_one(_python, _package, _mirrors, progress_callback):
            progress_callback(40)
            progress_callback(10)
            return True, ""

        output = io.StringIO()
        with patch.object(install_deps, "DEPENDENCIES", dependencies), patch.object(
            install_deps, "_pkg_installed", return_value=False
        ), patch.object(
            install_deps, "_install_one", side_effect=install_one
        ), patch.object(
            install_deps, "_COLOR_ENABLED", False
        ), patch.object(
            rar_backend, "is_bundled_unrar_ready", return_value=True
        ), contextlib.redirect_stdout(output):
            result = install_deps.install_all("python.exe", [{"name": "test"}])

        text = output.getvalue()
        self.assertTrue(result)
        self.assertIn("0/2", text)
        self.assertIn("1/2", text)
        self.assertIn("2/2", text)

    def test_pip_23_compatible_progress_option_is_used(self):
        class FakeProcess:
            returncode = 0
            stdout = iter(())

            @staticmethod
            def poll():
                return 0

        with patch.object(
            install_deps.subprocess,
            "Popen",
            return_value=FakeProcess(),
        ) as popen:
            return_code, _output = install_deps._run_pip_install_with_progress(
                "python.exe",
                "ExamplePkg",
                {"url": "https://example.invalid/simple", "host": "example.invalid"},
                lambda _percent: None,
            )

        self.assertEqual(return_code, 0)
        command = popen.call_args.args[0]
        option_index = command.index("--progress-bar")
        self.assertEqual(command[option_index + 1], "off")
        self.assertNotIn("raw", command)

    def test_pip_progress_uses_monotonic_output_stages(self):
        values = [
            install_deps._pip_progress_from_output("", 5),
            install_deps._pip_progress_from_output("Collecting example", 5),
            install_deps._pip_progress_from_output("Downloading example", 22),
            install_deps._pip_progress_from_output(
                "Installing collected packages: example",
                48,
            ),
            install_deps._pip_progress_from_output(
                "Successfully installed example",
                78,
            ),
        ]

        self.assertEqual(values, [5, 22, 48, 78, 95])
        self.assertEqual(install_deps._pip_progress_from_output("", 78), 78)
        self.assertEqual(values, sorted(values))

    def test_runtime_install_stages_have_monotonic_bar_values(self):
        self.assertEqual(
            install_deps._runtime_install_stage("Collecting example", kind="pip"),
            (22, "解析 CUDA 依赖"),
        )
        self.assertEqual(
            install_deps._runtime_install_stage("Downloading example", kind="pip"),
            (48, "下载 CUDA 依赖"),
        )
        self.assertEqual(
            install_deps._runtime_install_stage("npm http fetch GET 200", kind="npm"),
            (48, "下载依赖"),
        )
        self.assertIsNone(install_deps._runtime_install_stage("warning", kind="pip"))

    def test_runtime_and_transfer_progress_use_the_shared_bar_style(self):
        output = io.StringIO()
        with patch.object(install_deps, "_COLOR_ENABLED", False), contextlib.redirect_stdout(output):
            display = install_deps._RuntimeInstallProgress("CUDA pip")
            display.update(48, "下载 CUDA 依赖", force=True)
            display.finish("完成", success=True)

        text = output.getvalue()
        self.assertIn("[━━━━━━━━━━━━━", text)
        self.assertIn("100%", text)
        self.assertIn("[━━━━━━━━━━━━━", install_deps._render_transfer_progress(
            "downloading", 50, 100, 0.0
        ))
        self.assertNotIn("#", install_deps._render_transfer_progress(
            "downloading", 50, 100, 0.0
        ))

    def test_monotonic_progress_reporter_ignores_mirror_retry_regression(self):
        values = []
        reporter = install_deps._MonotonicProgressReporter(values.append)

        for value in (5, 60, 10, 78, 100):
            reporter(value)

        self.assertEqual(values, [5, 60, 78, 100])

    def test_pip_without_output_does_not_fabricate_time_progress(self):
        class FakeProcess:
            returncode = 0
            stdout = iter(())

            @staticmethod
            def poll():
                return 0

        values = []
        with patch.object(
            install_deps.subprocess,
            "Popen",
            return_value=FakeProcess(),
        ):
            return_code, _output = install_deps._run_pip_requirement_with_progress(
                "python.exe",
                "ExamplePkg",
                values.append,
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(values, [5, 95])

    def test_opencc_install_requires_a_prebuilt_wheel(self):
        class FakeProcess:
            returncode = 0
            stdout = iter(())

            @staticmethod
            def poll():
                return 0

        with patch.object(
            install_deps.subprocess,
            "Popen",
            return_value=FakeProcess(),
        ) as popen:
            return_code, _output = install_deps._run_pip_install_with_progress(
                "python.exe",
                "opencc-python-reimplemented",
                {"url": "https://example.invalid/simple", "host": "example.invalid"},
                lambda _percent: None,
            )

        self.assertEqual(return_code, 0)
        command = popen.call_args.args[0]
        self.assertIn("opencc-python-reimplemented>=0.1.7,<1", command)
        option_index = command.index("--only-binary")
        self.assertEqual(
            command[option_index + 1],
            "opencc-python-reimplemented",
        )

    def test_failed_package_reason_is_shown_and_later_packages_continue(self):
        dependencies = [
            ("BrokenPkg", "broken", ()),
            ("LaterPkg", "later", ()),
        ]
        attempts = []

        def install_one(_python, package, _mirrors, _progress_callback):
            attempts.append(package)
            if package == "BrokenPkg":
                return False, "PyPI: ERROR: compiler unavailable"
            return True, ""

        output = io.StringIO()
        with patch.object(install_deps, "DEPENDENCIES", dependencies), patch.object(
            install_deps, "_pkg_installed", return_value=False
        ), patch.object(install_deps, "_install_one", side_effect=install_one), patch.object(
            install_deps, "_COLOR_ENABLED", False
        ), patch.object(
            rar_backend, "is_bundled_unrar_ready", return_value=True
        ), patch("builtins.input", return_value="n"), contextlib.redirect_stdout(output):
            result = install_deps.install_all("python.exe", [{"name": "PyPI"}])

        self.assertFalse(result)
        self.assertEqual(attempts, ["BrokenPkg", "LaterPkg"])
        self.assertIn("失败原因", output.getvalue())
        self.assertIn("BrokenPkg: PyPI: ERROR: compiler unavailable", output.getvalue())

    def test_opencc_manual_install_command_keeps_wheel_and_version_constraints(self):
        dependencies = [
            (
                "opencc-python-reimplemented",
                "Chinese script conversion",
                ("opencc",),
            ),
        ]
        output = io.StringIO()

        with patch.object(install_deps, "DEPENDENCIES", dependencies), patch.object(
            install_deps, "_pkg_installed", return_value=False
        ), patch.object(
            install_deps,
            "_install_one",
            return_value=(False, "PyPI: wheel unavailable"),
        ), patch.object(
            install_deps, "_COLOR_ENABLED", False
        ), patch.object(
            rar_backend, "is_bundled_unrar_ready", return_value=True
        ), patch("builtins.input", return_value="n"), contextlib.redirect_stdout(output):
            result = install_deps.install_all("python.exe", [{"name": "PyPI"}])

        self.assertFalse(result)
        self.assertIn(
            "python.exe -m pip install --only-binary opencc-python-reimplemented "
            "opencc-python-reimplemented>=0.1.7,<1",
            output.getvalue(),
        )

    def test_pip_failure_summary_prefers_actionable_error_lines(self):
        output = """Looking in indexes: https://example.invalid/simple
Collecting package
ERROR: Could not build wheels for package
ERROR: No matching distribution found for dependency
"""

        summary = install_deps._summarize_pip_failure(output)

        self.assertIn("Could not build wheels", summary)
        self.assertIn("No matching distribution", summary)
        self.assertNotIn("Looking in indexes", summary)

    def test_dependency_check_displays_current_package_count_and_progress_bar(self):
        dependencies = [
            ("FirstPkg", "first", ()),
            ("SecondPkg", "second", ()),
        ]
        output = io.StringIO()

        with patch.object(install_deps, "DEPENDENCIES", dependencies), patch.object(
            install_deps, "_pkg_installed", return_value=True
        ), patch.object(install_deps, "_COLOR_ENABLED", False), patch.object(
            rar_backend, "is_bundled_unrar_ready", return_value=True
        ), contextlib.redirect_stdout(output):
            result = install_deps.install_all("python.exe", [{"name": "test"}])

        text = output.getvalue()
        self.assertTrue(result)
        self.assertIn("正在检查依赖", text)
        self.assertIn("FirstPkg", text)
        self.assertIn("SecondPkg", text)
        self.assertIn("2/2", text)
        self.assertIn("100%", text)
        self.assertIn("[━━━━━━━━━━━━━━━━━━━━━━━━━━]", text)

    def test_progress_bar_has_stable_width(self):
        with patch.object(install_deps, "_COLOR_ENABLED", False):
            bar = install_deps._render_dependency_bar(50, 100, width=10)
        self.assertEqual(bar, "[━━━━━─────]")
        self.assertEqual(len(bar), 12)

    def test_progress_rows_use_distinct_pip_style_colors(self):
        output = io.StringIO()
        with patch.object(install_deps, "_COLOR_ENABLED", True), contextlib.redirect_stdout(output):
            display = install_deps._DependencyProgressDisplay()
            display.update("MissingPkg", 40, 1, 3)

        text = output.getvalue()
        self.assertIn(install_deps._COLOR_MAP["progress_current"], text)
        self.assertIn(install_deps._COLOR_MAP["progress_overall"], text)
        self.assertIn(install_deps._COLOR_MAP["progress_track"], text)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
        self.assertIn("[━━━━━━━━━━────────────────]", plain)
        self.assertIn("[━━━━━━━━━─────────────────]", plain)
        self.assertIn(" 40%  MissingPkg", plain)
        self.assertIn("1/3", plain)


if __name__ == "__main__":
    unittest.main()
