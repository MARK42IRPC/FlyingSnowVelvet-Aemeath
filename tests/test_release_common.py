import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.release_common import build_generated_payloads


class ReleaseCommonTests(unittest.TestCase):
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
