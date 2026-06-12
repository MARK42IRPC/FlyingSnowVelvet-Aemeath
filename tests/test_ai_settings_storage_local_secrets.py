import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.ui import ai_settings_storage as storage


class AISettingsStorageLocalSecretsTests(unittest.TestCase):
    def test_save_ai_values_writes_secrets_to_user_file_and_clears_config_fields(self):
        config_text = """API_KEY = 'legacy'
FORCE_REPLY_MODE = ''
API_BASE_URL = 'https://example.invalid/v1'
API_MODEL = 'model-a'
YUANBAO_FREE_API = {
    'login_url': 'https://yuanbao.tencent.com/chat/default',
    'hy_source': 'web',
    'hy_user': 'legacy-user',
    'x_uskey': 'legacy-uskey',
    'agent_id': 'default-agent',
}
OLLAMA = {
    'base_url': 'http://localhost:11434',
    'api_temperature': 0.8,
    'gsv_auto_start': True,
    'gsv_temperature': 1.35,
    'gsv_speed_factor': 1.0,
    'ai_voice_max_chars': 40,
    'gsv_cache_max_files': 20,
    'memory_context_limit': 12,
    'memory_recall_count': 5,
    'api_enable_thinking': False,
}
OLLAMA_MODEL = 'qwen2.5'
AUTO_COMPANION = {
    'enabled': True,
}
OLLAMA_OPTIONS = {
    'num_gpu': -1,
    'num_thread': 0,
}
"""
        values = {
            'api_key': 'new-api-key',
            'force_reply_mode': '4',
            'api_base_url': 'http://127.0.0.1:8000/v1',
            'api_model': 'deepseek-v3',
            'yuanbao_login_url': 'https://yuanbao.tencent.com/chat/custom-agent',
            'yuanbao_hy_source': 'mobile',
            'yuanbao_hy_user': 'user-123',
            'yuanbao_x_uskey': 'secret-uskey',
            'yuanbao_agent_id': 'custom-agent',
            'ollama_base_url': 'http://localhost:11434',
            'ollama_model': 'qwen2.5',
            'num_gpu': -1,
            'num_thread': 0,
            'api_temperature': 0.8,
            'gsv_auto_start': True,
            'gsv_temperature': 1.35,
            'gsv_speed_factor': 1.0,
            'ai_voice_max_chars': 40,
            'gsv_cache_max_files': 20,
            'memory_context_limit': 12,
            'memory_recall_count': 5,
            'api_enable_thinking': False,
            'auto_companion_enabled': True,
        }
        default_values = {'memory_context_limit': 12}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_dir = root / 'config'
            config_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = config_dir / 'ollama_config.py'
            cfg_path.write_text(config_text, encoding='utf-8')
            mirrored: list[tuple[str, str]] = []

            with patch.object(storage, '_project_root', return_value=root), patch.object(
                storage, '_mirror_config_text_to_shared', side_effect=lambda rel_name, text: mirrored.append((rel_name, text))
            ):
                storage.save_ai_values(values, default_values)

            updated_text = cfg_path.read_text(encoding='utf-8')
            self.assertIn("API_KEY = ''", updated_text)
            self.assertIn("'hy_user': ''", updated_text)
            self.assertIn("'x_uskey': ''", updated_text)
            self.assertIn("FORCE_REPLY_MODE = '4'", updated_text)
            self.assertIn("'agent_id': 'custom-agent'", updated_text)

            secret_path = root / 'resc' / 'user' / 'ai' / 'ollama_secrets.json'
            secret_payload = json.loads(secret_path.read_text(encoding='utf-8'))
            self.assertEqual(secret_payload['api_key'], 'new-api-key')
            self.assertEqual(secret_payload['yuanbao_hy_user'], 'user-123')
            self.assertEqual(secret_payload['yuanbao_x_uskey'], 'secret-uskey')

            self.assertEqual(len(mirrored), 1)
            self.assertEqual(mirrored[0][0], 'ollama_config.py')
            self.assertIn("API_KEY = ''", mirrored[0][1])


if __name__ == '__main__':
    unittest.main()
