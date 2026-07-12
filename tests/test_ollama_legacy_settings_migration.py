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
                """YUANBAO_FREE_API = {
    'login_url': 'https://example.test/chat/custom',
    'hy_user': _LOCAL_SECRET_OVERRIDES.get('yuanbao_hy_user', ''),
    'x_uskey': _LOCAL_SECRET_OVERRIDES.get('yuanbao_x_uskey', ''),
    'agent_id': 'custom-agent',
}
""",
                encoding="utf-8",
            )
            payload = oc._literal_python_config(path)

        self.assertEqual(payload["YUANBAO_FREE_API"], {
            "login_url": "https://example.test/chat/custom",
            "agent_id": "custom-agent",
        })


if __name__ == "__main__":
    unittest.main()
