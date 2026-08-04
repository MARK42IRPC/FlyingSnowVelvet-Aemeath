from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config.config as config_module
import config.user_settings as user_settings
from config.general_user_settings import get_general_setting_defaults, save_general_values


class GeneralUserSettingsSparseTests(unittest.TestCase):
    def test_save_general_values_does_not_modify_python_defaults(self):
        defaults = get_general_setting_defaults()
        original = config_module.CLOUD_MUSIC["provider"]
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path):
                sparse = save_general_values({"CLOUD_MUSIC": {"provider": "qq"}})

            self.assertEqual(sparse["CLOUD_MUSIC"]["provider"], "qq")
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["overrides"]["general"]["CLOUD_MUSIC"]["provider"], "qq")
            self.assertEqual(defaults["CLOUD_MUSIC"]["provider"], original)

        config_module.CLOUD_MUSIC["provider"] = original

    def test_tuple_defaults_round_trip_through_json_lists(self):
        defaults = {"range": (1, 2)}
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            user_settings.save_section("test", {"range": (2, 3)}, defaults, path=settings_path)
            self.assertEqual(
                user_settings.load_section("test", defaults, path=settings_path)["range"],
                (2, 3),
            )

    def test_workbench_theme_is_a_persisted_ui_switch(self):
        defaults = get_general_setting_defaults()
        self.assertFalse(defaults["UI"]["workbench_light_theme"])
        original_theme = config_module.UI["workbench_light_theme"]
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path):
                save_general_values({"UI": {"workbench_light_theme": True}})
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["overrides"]["general"]["UI"]["workbench_light_theme"])
        config_module.UI["workbench_light_theme"] = original_theme

    def test_render_backend_is_a_sparse_persisted_choice(self):
        defaults = get_general_setting_defaults()
        self.assertEqual(defaults["UI"]["render_backend"], "qt")
        original_backend = config_module.UI["render_backend"]
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path):
                save_general_values({"UI": {"render_backend": "directx"}})
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["overrides"]["general"]["UI"]["render_backend"],
                "directx",
            )
        config_module.UI["render_backend"] = original_backend


if __name__ == "__main__":
    unittest.main()
