"""Tests for modules that must work without a Qt application or a window."""

from __future__ import annotations

import ast
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import version_info
from lib.core.services import api_endpoints, network_policy, ui_presentation
from scripts import release_common
from scripts.build_offline_distribution import PRODUCT_ROOT, excluded

ROOT = Path(__file__).resolve().parent.parent


class VersionContractTests(unittest.TestCase):
    """``config/version_info.py`` is the release authority."""

    def test_app_version_is_a_release_identifier(self):
        self.assertRegex(version_info.APP_VERSION, r"^(LTS)?\d+\.\d+\.\d+")

    def test_resource_version_tracks_app_version_by_default(self):
        self.assertEqual(version_info.RESOURCE_VERSION, version_info.APP_VERSION)
        self.assertEqual(version_info.RESOURCE_RELEASE_DATE, version_info.APP_RELEASE_DATE)

    def test_release_reads_version_without_importing_the_config_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_root = root / "config"
            config_root.mkdir()
            (config_root / "__init__.py").write_text(
                "raise RuntimeError('config must not be imported here')\n",
                encoding="utf-8",
            )
            (config_root / "version_info.py").write_text(
                "APP_VERSION = 'LTS9.9.9pre1'\n", encoding="utf-8"
            )
            self.assertEqual(release_common.read_app_version(root), "LTS9.9.9pre1")

    def test_release_console_reconfigures_both_streams_to_utf8(self):
        class Stream:
            def __init__(self):
                self.options = None

            def reconfigure(self, **options):
                self.options = options

        stdout, stderr = Stream(), Stream()
        with patch("scripts.release_common.sys.stdout", stdout), patch(
            "scripts.release_common.sys.stderr", stderr
        ):
            release_common.configure_console_output()
        expected = {"encoding": "utf-8", "errors": "backslashreplace"}
        self.assertEqual(stdout.options, expected)
        self.assertEqual(stderr.options, expected)


class ReleasePayloadBoundaryTests(unittest.TestCase):
    """What may enter ``app/`` in an offline package, and what must not."""

    def assertIncluded(self, relative: str):
        path = PRODUCT_ROOT / relative
        self.assertTrue(path.is_file(), relative)
        self.assertFalse(excluded(Path(relative)), relative)

    def assertExcluded(self, relative: str):
        self.assertTrue(excluded(Path(relative)), relative)

    def test_update_installer_and_managed_prompt_are_shipped(self):
        self.assertIncluded("lib/script/app/update_installer.py")
        self.assertIncluded("resc/agent/office_system_prompt.txt")

    def test_unrar_is_shipped_with_its_license(self):
        self.assertIncluded("lib/script/gsvmove/bin/UnRAR.exe")
        self.assertIncluded("lib/script/gsvmove/bin/LICENSE-UnRAR.txt")

    def test_dsh_runtime_sources_ship_but_node_modules_do_not(self):
        from lib.core.dsh_runtime_contract import RUNTIME_SOURCE_FILES

        for relative in RUNTIME_SOURCE_FILES:
            with self.subTest(relative=relative):
                self.assertIncluded(f"services/dsh-office-runtime/{relative}")
        self.assertExcluded("services/dsh-office-runtime/node_modules/package.json")
        self.assertExcluded("resc/node-24.13.0-win-x64/node.exe")

    def test_development_scratch_files_stay_out_of_the_root_payload(self):
        for relative in ("fulltest.log", "py.ini", ".tmp-office-settings.png"):
            with self.subTest(relative=relative):
                self.assertExcluded(relative)
        # The rule is scoped to the repository root.
        self.assertFalse(excluded(Path("lib/script/runtime.log")))

    def test_local_preview_pages_stay_out_of_the_payload(self):
        for relative in (
            ".localpage/index.html",
            ".localpage/assets/media/pet-idle.gif",
            "local_pages/index.html",
        ):
            with self.subTest(relative=relative):
                self.assertExcluded(relative)


class SharedPresentationNumbersTests(unittest.TestCase):
    """Numbers shared by every backend resolve through one function."""

    def test_ui_fade_duration_has_a_single_documented_default(self):
        self.assertEqual(ui_presentation.DEFAULT_UI_FADE_DURATION_MS, 200)
        self.assertEqual(ui_presentation.ui_fade_duration_ms(0), 0)
        self.assertEqual(ui_presentation.ui_fade_duration_ms("350"), 350)
        self.assertEqual(ui_presentation.ui_fade_duration_ms(-5), 0)
        self.assertEqual(
            ui_presentation.ui_fade_duration_ms("not a number"),
            ui_presentation.DEFAULT_UI_FADE_DURATION_MS,
        )

    def test_overlay_hide_linger_is_shared(self):
        self.assertGreater(ui_presentation.DEFAULT_OVERLAY_HIDE_LINGER_MS, 0)


class NetworkPolicyTests(unittest.TestCase):
    """The AI endpoints use one timeout and one retry budget."""

    def test_retry_budget_is_attempts_minus_the_first_call(self):
        self.assertGreater(network_policy.API_TIMEOUT_SECS, 0)
        self.assertEqual(
            network_policy.API_TOTAL_ATTEMPTS,
            network_policy.API_RETRY_COUNT + 1,
        )

    def test_api_endpoint_service_is_backend_neutral(self):
        source = Path(api_endpoints.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        imported |= {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("PyQt5", imported)
        self.assertNotIn("lib.script", imported)


class SupportImportTests(unittest.TestCase):
    """The helpers imported by this layer must not load Qt by themselves."""

    def test_support_process_module_imports(self):
        module = importlib.import_module("tests_ng.support.process")
        self.assertTrue(callable(module.process_is_alive))


if __name__ == "__main__":
    unittest.main()
