from __future__ import annotations

import json
import os
import subprocess
import sys
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
            "office_api_key": "office-api-key",
            "api_base_url": "http://127.0.0.1:8000/v1",
            "api_model": "deepseek-v3",
            "yuanbao_hy_user": "user-123",
            "yuanbao_x_uskey": "secret-uskey",
            "force_reply_mode": "0",
            "welfare_intelligence_boost": True,
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
            self.assertEqual(ai["force_reply_mode"], "0")
            self.assertTrue(ai["welfare_intelligence_boost"])
            self.assertNotIn("yuanbao_agent_id", ai)
            self.assertNotIn("gsv_top_k", ai)
            self.assertNotIn("gsv_fragment_interval", ai)
            self.assertNotIn("ollama_model", ai)
            self.assertNotIn("api_key", ai)
            self.assertNotIn("yuanbao_hy_user", ai)

            secrets = json.loads(secret_path.read_text(encoding="utf-8"))
            self.assertEqual(secrets, {
                "api_key": "new-api-key",
                "office_api_key": "office-api-key",
            })

    def test_load_ai_values_refreshes_secrets_from_disk(self):
        defaults = oc.get_ai_setting_defaults()
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_path = Path(tmpdir) / "user" / "secrets" / "ai.json"
            secret_path.parent.mkdir(parents=True)
            secret_path.write_text(
                json.dumps({"api_key": "written-by-another-process"}),
                encoding="utf-8",
            )

            with patch.object(storage, "_local_ai_secret_path", return_value=secret_path), patch.object(
                oc, "API_KEY", "stale-import-time-key"
            ):
                loaded = storage.load_ai_values(defaults)

        self.assertEqual(loaded["api_key"], "written-by-another-process")

    def test_load_ai_values_includes_effective_onnx_parameters(self):
        defaults = oc.get_ai_setting_defaults()
        loaded = storage.load_ai_values(defaults)

        for key in (
            "gsv_temperature",
            "gsv_top_k",
            "gsv_top_p",
            "gsv_repetition_penalty",
            "gsv_speed_factor",
            "gsv_text_split_method",
            "gsv_fragment_interval",
            "gsv_seed",
            "gsv_max_steps",
            "gsv_gpu_hybrid",
        ):
            with self.subTest(key=key):
                self.assertIn(key, loaded)

    def test_apply_runtime_converts_auto_companion_minutes_to_milliseconds(self):
        defaults = oc.get_ai_setting_defaults()
        values = storage.load_ai_values(defaults)
        values["auto_companion_interval_minutes"] = 13

        original = dict(oc.AUTO_COMPANION)
        with patch("lib.script.chat.ollama.get_ollama_manager") as manager, patch(
            "lib.core.event.center.get_event_center"
        ):
            try:
                storage.apply_ai_runtime(values, defaults)
                self.assertEqual(oc.AUTO_COMPANION["interval_minutes"], 13)
                self.assertEqual(oc.AUTO_COMPANION["interval_ms"], (780000, 780000))
            finally:
                oc.AUTO_COMPANION.clear()
                oc.AUTO_COMPANION.update(original)
        manager.return_value.reload_config.assert_called_once_with()

    def test_saved_api_key_is_loaded_by_a_fresh_process(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            env = os.environ.copy()
            env["AEMEATH_DESK_PET_HOME"] = tmpdir
            env["PYTHONPATH"] = str(project_root)
            save_script = """
import config.ollama_config as oc
from lib.script.ui.ai_settings_storage import save_ai_values
values = oc.get_ai_setting_defaults()
values.update({
    'api_key': 'cross-process-key',
    'api_base_url': 'https://manual.example/v1',
    'api_model': 'manual-model',
    'force_reply_mode': '0',
})
save_ai_values(values, values)
"""
            load_script = "import config.ollama_config as oc; print('KEY=' + oc.API_KEY)"

            saved = subprocess.run(
                [sys.executable, "-c", save_script],
                cwd=project_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            loaded = subprocess.run(
                [sys.executable, "-c", load_script],
                cwd=project_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
        self.assertIn("KEY=cross-process-key", loaded.stdout)


if __name__ == "__main__":
    unittest.main()
