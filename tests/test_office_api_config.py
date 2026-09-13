from __future__ import annotations

import unittest
from unittest.mock import patch

import config.ollama_config as oc


class OfficeApiConfigTests(unittest.TestCase):
    def _independent_mode(self, **overrides):
        values = {
            "use_independent_api": True,
            "api_key": "",
            "api_base_url": "",
            "api_model": "",
        }
        values.update(overrides)
        return values

    def test_independent_api_never_reuses_the_reply_route(self):
        with patch.object(
            oc, "_load_local_secret_overrides", return_value={}
        ), patch.dict(
            oc.OFFICE_MODE,
            self._independent_mode(
                api_key="office-key",
                api_base_url="https://office.invalid/v1",
                api_model="office-model",
            ),
            clear=False,
        ), patch.object(
            oc,
            "get_active_config",
            side_effect=AssertionError("must not reuse the reply route"),
        ):
            config = oc.get_office_active_config()

        self.assertEqual(config["api_type"], "openai_compatible")
        self.assertEqual(config["api_key"], "office-key")
        self.assertEqual(config["base_url"], "https://office.invalid/v1")
        self.assertEqual(config["model"], "office-model")
        self.assertEqual(config["key_source"], "office_api")

    def test_independent_api_reads_the_office_key_from_the_secret_file(self):
        with patch.object(
            oc,
            "_load_local_secret_overrides",
            return_value={"office_api_key": "disk-office-key"},
        ), patch.dict(
            oc.OFFICE_MODE,
            self._independent_mode(
                api_base_url="https://office.invalid/v1",
                api_model="office-model",
            ),
            clear=False,
        ):
            config = oc.get_office_active_config()

        self.assertEqual(config["api_key"], "disk-office-key")

    def test_incomplete_independent_api_fails_closed(self):
        with patch.object(
            oc, "_load_local_secret_overrides", return_value={}
        ), patch.dict(
            oc.OFFICE_MODE,
            self._independent_mode(api_model="office-model"),
            clear=False,
        ), patch.object(
            oc,
            "get_active_config",
            side_effect=AssertionError("must not reuse the reply route"),
        ):
            config = oc.get_office_active_config()

        self.assertEqual(config["api_type"], "error")
        self.assertIn("接口密钥", config["error"])
        self.assertIn("接口地址", config["error"])
        self.assertNotIn("接口模型", config["error"])

    def test_welfare_api_is_rejected_when_independent_api_is_off(self):
        with patch.dict(
            oc.OFFICE_MODE, {"use_independent_api": False}, clear=False
        ), patch.object(
            oc,
            "get_active_config",
            return_value={
                "api_type": "openai_compatible",
                "key_source": "welfare_api",
                "error": "",
            },
        ):
            config = oc.get_office_active_config()

        self.assertEqual(config["api_type"], "error")
        self.assertEqual(config["error"], oc.OFFICE_API_WELFARE_REJECTED)

    def test_manual_api_passes_through_when_independent_api_is_off(self):
        manual = {
            "api_type": "openai_compatible",
            "key_source": "config_api",
            "error": "",
        }
        with patch.dict(
            oc.OFFICE_MODE, {"use_independent_api": False}, clear=False
        ), patch.object(oc, "get_active_config", return_value=manual):
            config = oc.get_office_active_config()

        self.assertIs(config, manual)


if __name__ == "__main__":
    unittest.main()
