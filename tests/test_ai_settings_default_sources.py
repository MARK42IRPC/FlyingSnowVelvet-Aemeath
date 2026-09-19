"""默认值只有一份真源的守卫测试。

AI 设置面板与办公页共用 `ai_settings_defaults.AI_DEFAULT_VALUES`，语音面板与 gsvmove
运行时又各自需要同一批 ONNX 默认值。历史上这些值在四个地方各写了一遍（config、
`AI_DEFAULT_VALUES`、`ai_settings_panel` 的 `or "1.35"` 兜底、`gsvmove/service.py` 的
`oc.OLLAMA.get(..., 15)`）。这里的用例确保它们都从 config 派生，任何一处再抄一份就红。
"""

import unittest
from unittest.mock import patch

import config.ollama_config as oc
from lib.script.gsvmove import service as service_module
from lib.script.ui.ai_settings_defaults import AI_DEFAULT_VALUES, build_ai_default_values


class AiDefaultValuesDerivationTests(unittest.TestCase):
    def test_key_set_matches_the_documented_exceptions(self):
        full = oc.get_ai_setting_defaults()
        self.assertEqual(set(AI_DEFAULT_VALUES) - set(full), {"api_key"})
        self.assertEqual(set(full) - set(AI_DEFAULT_VALUES), {"office_backend"})

    def test_every_shared_value_comes_from_config(self):
        full = oc.get_ai_setting_defaults()
        for key, value in AI_DEFAULT_VALUES.items():
            if key == "api_key":
                continue
            with self.subTest(key=key):
                self.assertEqual(value, full[key])

    def test_table_follows_a_changed_config_default(self):
        # 改 config 一处，本表必须立刻跟随；若不跟随说明又出现了硬编码副本。
        with patch.dict(oc._AI_SETTING_DEFAULTS, {"gsv_top_k": 77}, clear=False):
            self.assertEqual(build_ai_default_values()["gsv_top_k"], 77)

    def test_office_backend_is_owned_by_the_office_storage_layer(self):
        from lib.script.ui.ai_settings_storage import OFFICE_VALUE_KEYS

        self.assertIn("office_backend", OFFICE_VALUE_KEYS)
        self.assertNotIn("office_backend", AI_DEFAULT_VALUES)
        self.assertSetEqual(set(oc.OFFICE_SETTING_KEYS), set(OFFICE_VALUE_KEYS))


class GsvmoveShippedDefaultTests(unittest.TestCase):
    def test_shipped_default_reads_config(self):
        for key in ("gsv_temperature", "gsv_top_k", "gsv_top_p", "gsv_seed"):
            with self.subTest(key=key):
                self.assertEqual(service_module._shipped_default(key), oc.get_ai_setting_defaults()[key])

    def test_missing_config_falls_back_to_shipped_defaults_not_literals(self):
        removed = {
            "gsv_top_k": None,
            "gsv_top_p": None,
            "gsv_repetition_penalty": None,
            "gsv_fragment_interval": None,
            "gsv_seed": None,
        }
        with patch.dict(service_module.oc.OLLAMA, removed, clear=False):
            defaults = service_module._get_gsv_inference_defaults()

        shipped = oc.get_ai_setting_defaults()
        self.assertEqual(defaults["top_k"], shipped["gsv_top_k"])
        self.assertEqual(defaults["top_p"], shipped["gsv_top_p"])
        self.assertEqual(defaults["repetition_penalty"], shipped["gsv_repetition_penalty"])
        self.assertEqual(defaults["fragment_interval"], shipped["gsv_fragment_interval"])
        self.assertEqual(defaults["seed"], shipped["gsv_seed"])

    def test_cache_limit_falls_back_to_shipped_default(self):
        shipped = int(oc.get_ai_setting_defaults()["gsv_cache_max_files"])
        with patch.dict(service_module.oc.OLLAMA, {"gsv_cache_max_files": None}, clear=False):
            self.assertEqual(service_module._get_gsv_cache_max_files(), shipped)
        with patch.dict(service_module.oc.OLLAMA, {"gsv_cache_max_files": 9999}, clear=False):
            self.assertEqual(service_module._get_gsv_cache_max_files(), 128)


if __name__ == "__main__":
    unittest.main()
