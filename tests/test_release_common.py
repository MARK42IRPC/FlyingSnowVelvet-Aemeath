import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.release_common import build_generated_payloads, configure_console_output
from scripts.package_green_release import _should_exclude as green_should_exclude
from scripts.package_release import ROOT, _should_exclude as release_should_exclude


class ReleaseCommonTests(unittest.TestCase):
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

    def test_service_bundle_is_generated_from_current_source_tree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = root / 'services' / 'yuanbao-free-api'
            service.mkdir(parents=True)
            (service / 'app.py').write_text("VERSION = 'current'\n", encoding='utf-8')
            (service / 'requirements.txt').write_text('fastapi\n', encoding='utf-8')
            (service / '__pycache__').mkdir()
            (service / '__pycache__' / 'app.pyc').write_bytes(b'stale')

            payloads = build_generated_payloads(root)
            payload = payloads[Path('services/bundles/yuanbao-free-api-main.zip')]
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertEqual(
                    archive.read('app.py').decode('utf-8').splitlines(),
                    ["VERSION = 'current'"],
                )
                self.assertNotIn('__pycache__/app.pyc', archive.namelist())


if __name__ == '__main__':
    unittest.main()
