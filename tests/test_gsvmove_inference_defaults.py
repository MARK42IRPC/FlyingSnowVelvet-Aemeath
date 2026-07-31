import unittest
from unittest.mock import patch

from lib.script.gsvmove import service as service_module


class GsvmoveInferenceDefaultsTests(unittest.TestCase):
    def test_all_effective_panel_parameters_are_forwarded(self):
        configured = {
            "gsv_temperature": 0.82,
            "gsv_top_k": 31,
            "gsv_top_p": 0.91,
            "gsv_repetition_penalty": 1.2,
            "gsv_speed_factor": 1.1,
            "gsv_text_split_method": "cut2",
            "gsv_fragment_interval": 0.45,
            "gsv_seed": 42,
            "gsv_max_steps": 640,
        }
        with patch.dict(service_module.oc.OLLAMA, configured, clear=False):
            defaults = service_module._get_gsv_inference_defaults()

        self.assertEqual(defaults, {
            "temperature": 0.82,
            "top_k": 31,
            "top_p": 0.91,
            "repetition_penalty": 1.2,
            "speed_factor": 1.1,
            "text_split_method": "cut2",
            "fragment_interval": 0.45,
            "seed": 42,
            "max_steps": 640,
        })

    def test_invalid_config_values_fall_back_or_clamp(self):
        configured = {
            "gsv_top_k": 9999,
            "gsv_top_p": -1,
            "gsv_text_split_method": "unknown",
            "gsv_seed": "invalid",
            "gsv_max_steps": 1,
        }
        with patch.dict(service_module.oc.OLLAMA, configured, clear=False):
            defaults = service_module._get_gsv_inference_defaults()

        self.assertEqual(defaults["top_k"], 1025)
        self.assertEqual(defaults["top_p"], 0.01)
        self.assertEqual(defaults["text_split_method"], "cut5")
        self.assertEqual(defaults["seed"], -1)
        self.assertEqual(defaults["max_steps"], 64)


if __name__ == "__main__":
    unittest.main()
