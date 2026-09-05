import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.release_common import (
    configure_console_output,
    read_app_version,
)
from scripts.build_offline_distribution import PRODUCT_ROOT, excluded
from lib.core.dsh_runtime_contract import RUNTIME_SOURCE_FILES


class ReleaseCommonTests(unittest.TestCase):
    def test_read_app_version_does_not_import_config_package(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_root = root / 'config'
            config_root.mkdir()
            (config_root / '__init__.py').write_text(
                "raise RuntimeError('config package must not be imported')\n",
                encoding='utf-8',
            )
            (config_root / 'version_info.py').write_text(
                "APP_VERSION = 'LTS-test'\n",
                encoding='utf-8',
            )

            self.assertEqual(read_app_version(root), 'LTS-test')

    def test_console_output_uses_utf8_with_safe_error_fallback(self):
        class ReconfigurableStream:
            def __init__(self):
                self.options = None

            def reconfigure(self, **options):
                self.options = options

        stdout = ReconfigurableStream()
        stderr = ReconfigurableStream()
        with patch('scripts.release_common.sys.stdout', stdout), patch(
            'scripts.release_common.sys.stderr', stderr
        ):
            configure_console_output()

        expected = {'encoding': 'utf-8', 'errors': 'backslashreplace'}
        self.assertEqual(stdout.options, expected)
        self.assertEqual(stderr.options, expected)

    def test_offline_payload_includes_the_update_installer(self):
        installer = PRODUCT_ROOT / 'lib' / 'script' / 'app' / 'update_installer.py'
        self.assertTrue(installer.is_file())
        self.assertFalse(excluded(installer.relative_to(PRODUCT_ROOT)))

    def test_release_packages_include_bundled_unrar_and_license(self):
        for relative in (
            Path('lib/script/gsvmove/bin/UnRAR.exe'),
            Path('lib/script/gsvmove/bin/LICENSE-UnRAR.txt'),
        ):
            path = PRODUCT_ROOT / relative
            with self.subTest(path=relative.as_posix()):
                self.assertTrue(path.is_file())
                self.assertFalse(excluded(relative))

    def test_release_packages_include_dsh_sources_but_not_installed_runtimes(self):
        for relative in RUNTIME_SOURCE_FILES:
            path = PRODUCT_ROOT / "services" / "dsh-office-runtime" / relative
            with self.subTest(path=relative):
                self.assertTrue(path.is_file())
                self.assertFalse(excluded(Path("services/dsh-office-runtime") / relative))

        excluded_paths = (
            PRODUCT_ROOT / "services" / "dsh-office-runtime" / "node_modules" / "package.json",
            PRODUCT_ROOT / "resc" / "node-24.13.0-win-x64" / "node.exe",
        )
        for path in excluded_paths:
            with self.subTest(path=path):
                self.assertTrue(excluded(path.relative_to(PRODUCT_ROOT)))

    def test_release_packages_include_managed_office_system_prompt(self):
        prompt = PRODUCT_ROOT / "resc" / "agent" / "office_system_prompt.txt"

        self.assertTrue(prompt.is_file())
        self.assertFalse(excluded(prompt.relative_to(PRODUCT_ROOT)))

    def test_release_packages_include_bundled_office_skills(self):
        skill_names = (
            "fsv-office-workflow",
            "fsv-desktop-pet-architecture",
            "fsv-safe-editing",
            "fsv-test-verification",
            "fsv-windows-powershell",
            "fsv-workbench-ui",
            "fsv-browser-ui-check",
            "fsv-browser-research",
            "fsv-dependency-maintenance",
            "fsv-release-validation",
        )
        for name in skill_names:
            path = PRODUCT_ROOT / "resc" / "agent" / name / "SKILL.md"
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
                self.assertFalse(excluded(path.relative_to(PRODUCT_ROOT)))

if __name__ == '__main__':
    unittest.main()
