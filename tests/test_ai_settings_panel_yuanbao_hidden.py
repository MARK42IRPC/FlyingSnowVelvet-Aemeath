import unittest

from lib.script.ui.ai_settings_panel import AISettingsPanel, _DEFAULT_VALUES, _GPU_MODE_AUTO


class _TextField:
    def __init__(self, value: str):
        self._value = value

    def text(self) -> str:
        return self._value


class _RawTextField(_TextField):
    def raw_text(self) -> str:
        return self._value


class _CheckField:
    def __init__(self, checked: bool):
        self._checked = checked

    def isChecked(self) -> bool:
        return self._checked


class _ComboField:
    def __init__(self, data, text: str = ""):
        self._data = data
        self._text = text or str(data or "")

    def currentData(self):
        return self._data

    def currentText(self) -> str:
        return self._text


class _DummyPanel:
    pass


def _build_dummy_panel() -> _DummyPanel:
    panel = _DummyPanel()
    panel._api_key = _RawTextField("local-api-key")
    panel._force_mode = _ComboField("4", "优先走元宝web(默认)")
    panel._api_base_url = _TextField("http://127.0.0.1:8000/v1")
    panel._api_model = _TextField("deepseek-v3")
    panel._yuanbao_free_api_enabled = _CheckField(True)
    panel._yuanbao_chat_id = _TextField("chat-001")
    panel._yuanbao_remove_conversation = _CheckField(False)
    panel._yuanbao_upload_images = _CheckField(True)
    panel._ollama_base_url = _TextField("http://localhost:11434")
    panel._ollama_model = _ComboField("qwen2.5", "qwen2.5")
    panel._gpu_mode = _ComboField(_GPU_MODE_AUTO, "自动")
    panel._num_thread = _TextField("0")
    panel._api_temperature = _TextField("0.8")
    panel._gsv_auto_start = _CheckField(True)
    panel._gsv_temperature = _TextField("1.35")
    panel._gsv_speed_factor = _TextField("1.0")
    panel._ai_voice_max_chars = _TextField("40")
    panel._gsv_cache_max_files = _TextField("20")
    panel._memory_context_limit = _TextField("12")
    panel._api_enable_thinking = _CheckField(False)
    panel._auto_companion_enabled = _CheckField(True)
    panel._validate_ai_values = lambda values: None
    panel._collect_hidden_yuanbao_values = lambda: AISettingsPanel._collect_hidden_yuanbao_values(panel)
    return panel


class AISettingsPanelYuanbaoHiddenValuesTests(unittest.TestCase):
    def test_collect_values_preserves_loaded_hidden_yuanbao_fields(self):
        panel = _build_dummy_panel()
        loaded_hidden = {
            "yuanbao_login_url": "https://yuanbao.tencent.com/chat/custom-agent",
            "yuanbao_hy_source": "mobile",
            "yuanbao_hy_user": "user-123",
            "yuanbao_x_uskey": "secret-uskey",
            "yuanbao_agent_id": "custom-agent",
        }

        AISettingsPanel._set_hidden_yuanbao_values(panel, loaded_hidden)
        values = AISettingsPanel._collect_values(panel)

        for key, expected in loaded_hidden.items():
            self.assertEqual(values[key], expected)

    def test_collect_hidden_yuanbao_values_falls_back_to_defaults(self):
        panel = _build_dummy_panel()

        AISettingsPanel._set_hidden_yuanbao_values(panel, {})
        values = AISettingsPanel._collect_hidden_yuanbao_values(panel)

        self.assertEqual(values["yuanbao_login_url"], _DEFAULT_VALUES["yuanbao_login_url"])
        self.assertEqual(values["yuanbao_hy_source"], _DEFAULT_VALUES["yuanbao_hy_source"])
        self.assertEqual(values["yuanbao_hy_user"], _DEFAULT_VALUES["yuanbao_hy_user"])
        self.assertEqual(values["yuanbao_x_uskey"], _DEFAULT_VALUES["yuanbao_x_uskey"])
        self.assertEqual(values["yuanbao_agent_id"], _DEFAULT_VALUES["yuanbao_agent_id"])


if __name__ == "__main__":
    unittest.main()
