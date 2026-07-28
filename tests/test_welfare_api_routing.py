import unittest
from unittest.mock import Mock, patch

import config.ollama_config as ollama_config
from config.config_animation import BEHAVIOR
from lib.script.chat.ollama_session import OllamaSessionMixin
from lib.script.ui.ai_settings_validators import validate_ai_values


class _StrictSession(OllamaSessionMixin):
    def __init__(self):
        self._use_api_key = True
        self._active_config = {
            "fallback_config": {
                "base_url": "https://welfare.example/v1",
                "api_key": "welfare-key",
                "model": "welfare-model",
            },
        }
        self._strict_mode = True
        self._signal = None
        self._openai_chat_api = Mock(side_effect=RuntimeError("manual API failed"))


class WelfareApiRoutingTests(unittest.TestCase):
    def test_welfare_mode_uses_downloaded_config(self):
        resolved = {
            "api_key": "downloaded-key",
            "base_url": "https://welfare.example/v1",
            "models": ("agnes-2.0-flash", "agnes-2.5-flash"),
        }
        with patch.object(ollama_config, "FORCE_REPLY_MODE", "1"), patch.object(
            ollama_config, "WELFARE_INTELLIGENCE_BOOST", False
        ), patch(
            "lib.script.chat.welfare_api_config.resolve_welfare_api_config",
            return_value=resolved,
        ):
            active = ollama_config.get_active_config()

        self.assertEqual(active["key_source"], "welfare_api")
        self.assertEqual(active["base_url"], resolved["base_url"])
        self.assertEqual(active["model"], "agnes-2.0-flash")
        self.assertEqual(active["api_key"], resolved["api_key"])

    def test_welfare_intelligence_boost_uses_25_flash(self):
        resolved = {
            "api_key": "downloaded-key",
            "base_url": "https://welfare.example/v1",
            "models": ("agnes-2.0-flash", "agnes-2.5-flash"),
        }
        with patch.object(ollama_config, "FORCE_REPLY_MODE", "1"), patch.object(
            ollama_config, "WELFARE_INTELLIGENCE_BOOST", True
        ), patch(
            "lib.script.chat.welfare_api_config.resolve_welfare_api_config",
            return_value=resolved,
        ):
            active = ollama_config.get_active_config()

        self.assertEqual(active["model"], "agnes-2.5-flash")

    def test_manual_mode_without_required_fields_is_error(self):
        with patch.object(ollama_config, "FORCE_REPLY_MODE", "0"), patch.object(
            ollama_config, "API_KEY", ""
        ), patch.object(ollama_config, "API_BASE_URL", ""), patch.object(
            ollama_config, "API_MODEL", ""
        ):
            active = ollama_config.get_active_config()

        self.assertEqual(active["api_type"], "error")
        self.assertIn("手动 API 配置不完整", active["error"])

    def test_manual_mode_has_no_welfare_fallback(self):
        with patch.object(ollama_config, "FORCE_REPLY_MODE", "0"), patch.object(
            ollama_config, "API_KEY", "manual-key"
        ), patch.object(ollama_config, "API_BASE_URL", "https://manual.example/v1"), patch.object(
            ollama_config, "API_MODEL", "manual-model"
        ):
            active = ollama_config.get_active_config()

        self.assertEqual(active["key_source"], "config_api")
        self.assertNotIn("fallback_config", active)

    def test_session_does_not_switch_source_after_failure(self):
        session = _StrictSession()
        session._run_stream_chat("hello", "persona", 1, False)

        self.assertEqual(session._openai_chat_api.call_count, 1)

    def test_default_pet_movement_speed_is_one_third_of_previous_values(self):
        self.assertEqual(BEHAVIOR["move_min_speed"], 2.5)
        self.assertEqual(BEHAVIOR["move_acceleration"], 0.25)
        self.assertEqual(BEHAVIOR["move_max_speed"], 5.0)

    def test_welfare_mode_validation_allows_blank_manual_api_config(self):
        values = ollama_config.get_ai_setting_defaults()
        values.update({
            "force_reply_mode": "1",
            "api_key": "",
            "api_base_url": "",
            "api_model": "",
        })

        validate_ai_values(values)

    def test_manual_mode_validation_requires_complete_config(self):
        values = ollama_config.get_ai_setting_defaults()
        values.update({
            "force_reply_mode": "0",
            "api_key": "",
            "api_base_url": "",
            "api_model": "",
        })

        with self.assertRaisesRegex(ValueError, "接口密钥不能为空"):
            validate_ai_values(values)


if __name__ == "__main__":
    unittest.main()
