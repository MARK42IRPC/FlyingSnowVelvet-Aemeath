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

    def test_layer_order_is_a_sparse_persisted_int_section(self):
        defaults = get_general_setting_defaults()
        self.assertIn('LAYER_VALUES', defaults)
        self.assertEqual(defaults['LAYER_VALUES']['MAIN_PET'], 850)
        original = config_module.LAYER_VALUES['MAIN_PET']
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path):
                save_general_values({"LAYER_VALUES": {"MAIN_PET": 700}})
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["overrides"]["general"]["LAYER_VALUES"]["MAIN_PET"],
                700,
            )
        config_module.LAYER_VALUES['MAIN_PET'] = original

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

    def test_live_stream_is_a_sparse_persisted_section(self):
        defaults = get_general_setting_defaults()
        self.assertIn("LIVE_STREAM", defaults)
        self.assertEqual(defaults["LIVE_STREAM"]["command_prefix"], "点歌")
        original_room = config_module.LIVE_STREAM["room_id"]
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                settings_path = Path(tmpdir) / "settings.json"
                with patch.object(user_settings, "get_user_settings_path", return_value=settings_path):
                    sparse = save_general_values({
                        "LIVE_STREAM": {"room_id": "123", "request_cooldown_seconds": 20}
                    })
                self.assertEqual(sparse["LIVE_STREAM"]["room_id"], "123")
                payload = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    payload["overrides"]["general"]["LIVE_STREAM"]["room_id"],
                    "123",
                )
        finally:
            config_module.LIVE_STREAM["room_id"] = original_room

    def test_live_mode_switch_migrates_from_the_old_song_switch(self):
        """老配置只有 `enabled`，升级后总开关要继承它，否则直播会静默失效。

        新增 `live_mode_enabled` 默认 False；如果不管旧配置，升级后弹幕连接不再建立，
        用户看到的是「更新之后直播点歌突然不工作了」。
        """
        import config.general_user_settings as gus

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text(json.dumps({
                "migrations": {},
                "overrides": {"general": {"LIVE_STREAM": {"enabled": True, "room_id": "23466627"}}},
                "schema_version": 1,
            }), encoding="utf-8")
            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path):
                gus._migrate_live_mode_switch()
                payload = json.loads(settings_path.read_text(encoding="utf-8"))
                live_values = payload["overrides"]["general"]["LIVE_STREAM"]
                self.assertTrue(live_values["live_mode_enabled"])
                self.assertEqual(live_values["room_id"], "23466627")
                self.assertTrue(payload["migrations"]["live_mode_switch_v1"])

                # 迁移只做一次：之后用户手动关掉总开关，重启不会又被改回来。
                live_values["live_mode_enabled"] = False
                user_settings.save_section(
                    "general",
                    payload["overrides"]["general"],
                    gus.get_general_setting_defaults(),
                    path=settings_path,
                )
                gus._migrate_live_mode_switch()
                # 稀疏存储会把「等于默认值」的键省掉，所以按生效值核对而不是原始 JSON。
                effective = user_settings.load_section(
                    "general", gus.get_general_setting_defaults()
                )
                self.assertFalse(effective["LIVE_STREAM"]["live_mode_enabled"])

    def test_live_mode_migration_respects_an_explicit_switch(self):
        """用户已经在面板里设过总开关时，迁移不能覆盖他的选择。"""
        import config.general_user_settings as gus

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text(json.dumps({
                "migrations": {},
                "overrides": {"general": {"LIVE_STREAM": {
                    "enabled": True,
                    "live_mode_enabled": False,
                }}},
                "schema_version": 1,
            }), encoding="utf-8")
            with patch.object(user_settings, "get_user_settings_path", return_value=settings_path):
                gus._migrate_live_mode_switch()
                payload = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertFalse(
                    payload["overrides"]["general"]["LIVE_STREAM"]["live_mode_enabled"]
                )


if __name__ == "__main__":
    unittest.main()
