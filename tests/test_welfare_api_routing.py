import unittest
from unittest.mock import Mock, patch

import config.ollama_config as ollama_config
from config.config_animation import BEHAVIOR
from lib.script.chat.ollama_session import OllamaSessionMixin
from lib.script.ui.ai_settings_validators import validate_ai_values


class _FallbackSession(OllamaSessionMixin):
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
        self._openai_chat_api = Mock(side_effect=[RuntimeError("primary failed"), "fallback reply"])


class WelfareApiRoutingTests(unittest.TestCase):
    def test_welfare_mode_is_default_route(self):
        with patch.object(ollama_config, "FORCE_REPLY_MODE", "1"):
            active = ollama_config.get_active_config()

        self.assertEqual(active["key_source"], "welfare_api")
        self.assertEqual(active["base_url"], "https://apihub.agnes-ai.com/v1")
        self.assertEqual(active["model"], "agnes-2.0-flash")
        self.assertEqual(active["api_key"], "sk-welfare-api-not-configured")

    def test_manual_mode_without_required_fields_uses_welfare_api(self):
        with patch.object(ollama_config, "FORCE_REPLY_MODE", "0"), patch.object(
            ollama_config, "API_KEY", ""
        ), patch.object(ollama_config, "API_BASE_URL", ""), patch.object(
            ollama_config, "API_MODEL", ""
        ):
            active = ollama_config.get_active_config()

        self.assertEqual(active["key_source"], "welfare_api")

    def test_manual_mode_has_welfare_fallback(self):
        with patch.object(ollama_config, "FORCE_REPLY_MODE", "0"), patch.object(
            ollama_config, "API_KEY", "manual-key"
        ), patch.object(ollama_config, "API_BASE_URL", "https://manual.example/v1"), patch.object(
            ollama_config, "API_MODEL", "manual-model"
        ):
            active = ollama_config.get_active_config()

        self.assertEqual(active["key_source"], "config_api")
        self.assertEqual(active["fallback_config"]["key_source"], "welfare_api")

    def test_session_retries_with_welfare_config_after_primary_failure(self):
        session = _FallbackSession()
        session._run_stream_chat("hello", "persona", 1, False)

        self.assertEqual(session._openai_chat_api.call_count, 2)
        second_call = session._openai_chat_api.call_args_list[1]
        self.assertEqual(second_call.kwargs["config_override"], session._active_config["fallback_config"])

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

    def test_manual_mode_validation_allows_welfare_fallback_without_manual_config(self):
        values = ollama_config.get_ai_setting_defaults()
        values.update({
            "force_reply_mode": "0",
            "api_key": "",
            "api_base_url": "",
            "api_model": "",
        })

        validate_ai_values(values)


if __name__ == "__main__":
    unittest.main()
