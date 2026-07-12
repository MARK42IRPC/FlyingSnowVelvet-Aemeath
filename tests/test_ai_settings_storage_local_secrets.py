from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config.ollama_config as oc
import config.user_settings as user_settings
from lib.script.ui import ai_settings_storage as storage


class AISettingsStorageLocalSecretsTests(unittest.TestCase):
    def test_save_ai_values_writes_sparse_settings_and_separate_secrets(self):
        defaults = oc.get_ai_setting_defaults()
        values = {
            **defaults,
            "api_key": "new-api-key",
            "api_base_url": "http://127.0.0.1:8000/v1",
            "api_model": "deepseek-v3",
            "yuanbao_hy_user": "user-123",
            "yuanbao_x_uskey": "secret-uskey",
            "force_reply_mode": "4",
            "yuanbao_agent_id": "custom-agent",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            settings_path = root / "user" / "settings.json"
            secret_path = root / "user" / "secrets" / "ai.json"
            source_path = root / "ollama_config.py"
            source_path.write_text("SOURCE MUST NOT CHANGE\n", encoding="utf-8")

            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path), patch.object(
                storage, "_local_ai_secret_path", return_value=secret_path
            ):
                storage.save_ai_values(values, defaults)

            self.assertEqual(source_path.read_text(encoding="utf-8"), "SOURCE MUST NOT CHANGE\n")

            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            ai = settings["overrides"]["ai"]
            self.assertEqual(ai["api_base_url"], "http://127.0.0.1:8000/v1")
            self.assertEqual(ai["api_model"], "deepseek-v3")
            self.assertEqual(ai["force_reply_mode"], "4")
            self.assertEqual(ai["yuanbao_agent_id"], "custom-agent")
            self.assertNotIn("ollama_model", ai)
            self.assertNotIn("api_key", ai)
            self.assertNotIn("yuanbao_hy_user", ai)

            secrets = json.loads(secret_path.read_text(encoding="utf-8"))
            self.assertEqual(secrets, {
                "api_key": "new-api-key",
                "yuanbao_hy_user": "user-123",
                "yuanbao_x_uskey": "secret-uskey",
            })


if __name__ == "__main__":
    unittest.main()
