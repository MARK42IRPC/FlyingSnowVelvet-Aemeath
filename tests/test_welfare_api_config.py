import unittest
from unittest.mock import MagicMock, patch

from lib.script.chat import welfare_api_config as config


def _payload(name: str) -> dict[str, object]:
    return {
        "api_key": f"{name}-key",
        "base_url": f"https://{name}.example/v1",
        "models": (config.WELFARE_STANDARD_MODEL, config.WELFARE_BOOST_MODEL),
    }


class WelfareApiConfigTests(unittest.TestCase):
    def tearDown(self):
        config.clear_welfare_api_config_cache()

    def test_parse_release_file(self):
        parsed = config._parse_welfare_config(
            "secret-key\r\n\r\nhttps://api.example/v1\r\n\r\nmodel-a\r\nmodel-b\r\n"
        )

        self.assertEqual(parsed["api_key"], "secret-key")
        self.assertEqual(parsed["base_url"], "https://api.example/v1")
        self.assertNotIn("model", parsed)
        self.assertNotIn("models", parsed)

    def test_resolve_once_selects_fastest_valid_source(self):
        def download(url: str):
            if "github.com" in url:
                return 0.25, _payload("github")
            return 0.05, _payload("gitee")

        with patch.object(config, "_download_source", side_effect=download), patch.object(
            config,
            "_probe_available_models",
            return_value=(config.WELFARE_STANDARD_MODEL, config.WELFARE_BOOST_MODEL),
        ):
            resolved = config._resolve_once()

        self.assertEqual(resolved["api_key"], "gitee-key")
        self.assertIn("gitee.com", resolved["source_url"])
        self.assertEqual(resolved["latency_ms"], 50)
        self.assertEqual(
            resolved["models"],
            (config.WELFARE_STANDARD_MODEL, config.WELFARE_BOOST_MODEL),
        )

    def test_model_probe_uses_models_endpoint_and_ignores_release_model_lines(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.json.return_value = {
            "data": [
                {"id": config.WELFARE_STANDARD_MODEL},
                {"id": config.WELFARE_BOOST_MODEL},
            ]
        }
        with patch.object(config.requests, "get", return_value=response) as get:
            models = config._probe_available_models("https://api.example/v1", "secret-key")

        self.assertEqual(models, (config.WELFARE_STANDARD_MODEL, config.WELFARE_BOOST_MODEL))
        get.assert_called_once_with(
            "https://api.example/v1/models",
            headers={"Authorization": "Bearer secret-key"},
            timeout=10.0,
        )

    def test_model_selection_switches_between_standard_and_boost(self):
        models = (config.WELFARE_STANDARD_MODEL, config.WELFARE_BOOST_MODEL)
        self.assertEqual(config.select_welfare_model(models, False), config.WELFARE_STANDARD_MODEL)
        self.assertEqual(config.select_welfare_model(models, True), config.WELFARE_BOOST_MODEL)

    def test_model_selection_rejects_missing_requested_model(self):
        with self.assertRaisesRegex(RuntimeError, config.WELFARE_BOOST_MODEL):
            config.select_welfare_model((config.WELFARE_STANDARD_MODEL,), True)

    def test_resolver_retries_three_times_after_initial_failure(self):
        with patch.object(
            config,
            "_resolve_once",
            side_effect=[RuntimeError("1"), RuntimeError("2"), RuntimeError("3"), _payload("ok")],
        ) as resolve_once:
            resolved = config.resolve_welfare_api_config(force_refresh=True)

        self.assertEqual(resolve_once.call_count, 4)
        self.assertEqual(resolved["api_key"], "ok-key")

    def test_network_policy_is_hardcoded(self):
        self.assertEqual(config.API_TIMEOUT_SECS, 10.0)
        self.assertEqual(config.API_RETRY_COUNT, 3)
        self.assertEqual(config.API_TOTAL_ATTEMPTS, 4)


if __name__ == "__main__":
    unittest.main()
