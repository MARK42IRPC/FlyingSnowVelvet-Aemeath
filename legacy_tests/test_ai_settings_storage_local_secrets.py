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
    def test_office_value_keys_match_the_shared_defaults_table(self):
        """写盘键与 `config.ollama_config.OFFICE_SETTING_KEYS` 必须一一对应。"""
        self.assertEqual(
            set(storage.OFFICE_VALUE_KEYS),
            set(oc.OFFICE_SETTING_KEYS),
        )
        self.assertEqual(
            set(oc.get_office_setting_defaults()),
            set(oc.OFFICE_SETTING_KEYS),
        )
        # AI 面板拥有的默认值不含办公字段，整段加载用的默认值仍然含。
        self.assertFalse(
            [key for key in oc.get_ai_panel_setting_defaults() if key.startswith("office_")]
        )
        self.assertTrue(
            [key for key in oc.get_ai_setting_defaults() if key.startswith("office_")]
        )

    def test_ai_panel_save_keeps_office_config_and_secret(self):
        """AI 设置面板保存不碰办公字段：办公配置与办公密钥只由办公模式页写。"""
        defaults = oc.get_ai_setting_defaults()
        office_original = dict(oc.OFFICE_MODE)
        self.addCleanup(lambda: (oc.OFFICE_MODE.clear(), oc.OFFICE_MODE.update(office_original)))

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            settings_path = root / "user" / "settings.json"
            secret_path = root / "user" / "secrets" / "ai.json"
            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path), patch.object(
                storage, "_local_ai_secret_path", return_value=secret_path
            ), patch("lib.script.chat.ollama.get_ollama_manager"), patch(
                "lib.core.event.center.get_event_center"
            ):
                storage.save_office_values({
                    "office_backend": "local_dsh",
                    "office_use_independent_api": True,
                    "office_api_key": "office-secret",
                    "office_api_base_url": "https://office.example/v1",
                    "office_api_model": "office-model",
                    "office_warmup_on_startup": False,
                }, defaults)

                panel_values = {
                    key: value
                    for key, value in oc.get_ai_setting_defaults().items()
                    if not key.startswith("office_")
                }
                panel_values.update({"api_key": "panel-key", "api_model": "panel-model"})
                storage.save_ai_values(panel_values, defaults)

            ai = json.loads(settings_path.read_text(encoding="utf-8"))["overrides"]["ai"]
            self.assertEqual(ai["api_model"], "panel-model")
            self.assertEqual(ai["office_backend"], "local_dsh")
            self.assertTrue(ai["office_use_independent_api"])
            self.assertEqual(ai["office_api_model"], "office-model")
            self.assertEqual(ai["office_api_base_url"], "https://office.example/v1")
            self.assertFalse(ai["office_warmup_on_startup"])

            secrets = json.loads(secret_path.read_text(encoding="utf-8"))
            self.assertEqual(secrets["api_key"], "panel-key")
            self.assertEqual(secrets["office_api_key"], "office-secret")

    def test_apply_runtime_keeps_office_state_when_values_omit_office_keys(self):
        """面板保存时 values 里没有办公键，运行时办公状态不得被默认值顶掉。"""
        defaults = oc.get_ai_setting_defaults()
        office_original = dict(oc.OFFICE_MODE)
        self.addCleanup(lambda: (oc.OFFICE_MODE.clear(), oc.OFFICE_MODE.update(office_original)))
        oc.OFFICE_MODE.update({
            "backend": "local_dsh",
            "use_independent_api": True,
            "api_key": "kept-key",
            "api_base_url": "https://office.example/v1",
            "api_model": "kept-model",
            "warmup_on_startup": False,
        })
        values = {
            key: value
            for key, value in defaults.items()
            if not key.startswith("office_")
        }
        values["api_key"] = "panel-key"

        with patch("lib.script.chat.ollama.get_ollama_manager"), patch(
            "lib.core.event.center.get_event_center"
        ):
            storage.apply_ai_runtime(values, defaults)

        self.assertEqual(oc.API_KEY, "panel-key")
        self.assertEqual(oc.OFFICE_MODE["backend"], "local_dsh")
        self.assertTrue(oc.OFFICE_MODE["use_independent_api"])
        self.assertEqual(oc.OFFICE_MODE["api_key"], "kept-key")
        self.assertEqual(oc.OFFICE_MODE["api_base_url"], "https://office.example/v1")
        self.assertEqual(oc.OFFICE_MODE["api_model"], "kept-model")
        self.assertFalse(oc.OFFICE_MODE["warmup_on_startup"])

    def test_office_save_keeps_keys_the_form_did_not_provide(self):
        """办公页只提交半份表单时，没提交的办公键沿用当前生效值而不是回落到默认值。"""
        defaults = oc.get_ai_setting_defaults()
        office_original = dict(oc.OFFICE_MODE)
        self.addCleanup(lambda: (oc.OFFICE_MODE.clear(), oc.OFFICE_MODE.update(office_original)))

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            settings_path = root / "user" / "settings.json"
            secret_path = root / "user" / "secrets" / "ai.json"
            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path), patch.object(
                storage, "_local_ai_secret_path", return_value=secret_path
            ), patch("lib.script.chat.ollama.get_ollama_manager"), patch(
                "lib.core.event.center.get_event_center"
            ):
                storage.save_office_values({
                    "office_backend": "local_dsh",
                    "office_use_independent_api": True,
                    "office_api_key": "office-secret",
                    "office_api_base_url": "https://office.example/v1",
                    "office_api_model": "office-model",
                    "office_warmup_on_startup": False,
                }, defaults)
                merged = storage.save_office_values({"office_backend": "dsh"}, defaults)

            self.assertEqual(merged["office_backend"], "dsh")
            self.assertEqual(merged["office_api_model"], "office-model")
            self.assertEqual(merged["office_api_base_url"], "https://office.example/v1")
            self.assertTrue(merged["office_use_independent_api"])
            self.assertFalse(merged["office_warmup_on_startup"])
            self.assertEqual(oc.OFFICE_MODE["api_model"], "office-model")

            secrets = json.loads(secret_path.read_text(encoding="utf-8"))
            self.assertEqual(secrets["office_api_key"], "office-secret")

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
            "gsv_gpu_hybrid",
        ):
            with self.subTest(key=key):
                self.assertIn(key, loaded)

    def test_shipped_onnx_voice_defaults(self):
        defaults = oc.get_ai_setting_defaults()

        self.assertEqual(defaults["gsv_temperature"], 1.35)
        self.assertEqual(defaults["gsv_repetition_penalty"], 1.6)
        self.assertEqual(defaults["gsv_speed_factor"], 1.1)
        self.assertEqual(defaults["gsv_text_split_method"], "cut0")

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
