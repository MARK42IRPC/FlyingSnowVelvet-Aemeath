import contextlib
import hashlib
import io
import re
import subprocess
import unittest
from unittest.mock import patch

import install_deps
from lib.script.gsvmove import rar_backend


class InstallDependenciesProgressTests(unittest.TestCase):
    def test_python_311_is_preferred_over_current_non_target_runtime(self):
        with patch.object(install_deps, "_current_runtime_executable", return_value="C:\\Python312\\python.exe"):
            target = install_deps._sort_key(((3, 11, 6), "C:\\Python311\\python.exe"))
            current = install_deps._sort_key(((3, 12, 1), "C:\\Python312\\python.exe"))

        self.assertLess(target, current)

    def test_jieba_fast_prebuilt_wheel_is_ordered_before_genie_tts(self):
        names = [package for package, _description, _checks in install_deps.DEPENDENCIES]
        self.assertLess(names.index("jieba-fast"), names.index("genie-tts"))

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
