import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.release_common import (
    configure_console_output,
    read_app_version,
)
from scripts.package_green_release import _should_exclude as green_should_exclude
from scripts.package_release import ROOT, _should_exclude as release_should_exclude
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

    def test_release_packages_include_the_update_installer(self):
        installer = ROOT / 'lib' / 'script' / 'app' / 'update_installer.py'
        self.assertTrue(installer.is_file())
        self.assertFalse(release_should_exclude(installer))
        self.assertFalse(green_should_exclude(installer))

    def test_release_packages_include_bundled_unrar_and_license(self):
        for relative in (
            Path('lib/script/gsvmove/bin/UnRAR.exe'),
            Path('lib/script/gsvmove/bin/LICENSE-UnRAR.txt'),
        ):
            path = ROOT / relative
            with self.subTest(path=relative.as_posix()):
                self.assertTrue(path.is_file())
                self.assertFalse(release_should_exclude(path))
                self.assertFalse(green_should_exclude(path))

    def test_release_packages_include_dsh_sources_but_not_installed_runtimes(self):
        for relative in RUNTIME_SOURCE_FILES:
            path = ROOT / "services" / "dsh-office-runtime" / relative
            with self.subTest(path=relative):
                self.assertTrue(path.is_file())
                self.assertFalse(release_should_exclude(path))
                self.assertFalse(green_should_exclude(path))

        excluded = (
            ROOT / "services" / "dsh-office-runtime" / "node_modules" / "package.json",
            ROOT / "resc" / "node-24.13.0-win-x64" / "node.exe",
        )
        for path in excluded:
            with self.subTest(path=path):
                self.assertTrue(release_should_exclude(path))
                self.assertTrue(green_should_exclude(path))

    def test_release_packages_include_managed_office_system_prompt(self):
        prompt = ROOT / "resc" / "agent" / "office_system_prompt.txt"

        self.assertTrue(prompt.is_file())
        self.assertFalse(release_should_exclude(prompt))
        self.assertFalse(green_should_exclude(prompt))

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
            path = ROOT / "resc" / "agent" / name / "SKILL.md"
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
                self.assertFalse(release_should_exclude(path))
                self.assertFalse(green_should_exclude(path))

if __name__ == '__main__':
    unittest.main()
