from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import config.ollama_config as oc


class OllamaLegacySettingsMigrationTests(unittest.TestCase):
    def test_literal_parser_keeps_safe_dict_items_when_secrets_are_expressions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ollama_config.py"
            path.write_text(
                """OLLAMA = {
    'base_url': 'http://localhost:11434',
    'api_key': _LOCAL_SECRET_OVERRIDES.get('api_key', ''),
    'api_temperature': 0.8,
}
""",
                encoding="utf-8",
            )
            payload = oc._literal_python_config(path)

        self.assertEqual(payload["OLLAMA"], {
            "base_url": "http://localhost:11434",
            "api_temperature": 0.8,
        })


if __name__ == "__main__":
    unittest.main()
