import unittest
from unittest.mock import patch

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

        with patch.object(config, "_download_source", side_effect=download):
            resolved = config._resolve_once()

        self.assertEqual(resolved["api_key"], "gitee-key")
        self.assertIn("gitee.com", resolved["source_url"])
        self.assertEqual(resolved["latency_ms"], 50)
        self.assertEqual(
            resolved["models"],
            (config.WELFARE_STANDARD_MODEL, config.WELFARE_BOOST_MODEL),
        )

    def test_resolver_uses_fixed_models_without_models_endpoint_probe(self):
        with patch.object(config, "_download_source", return_value=(0.05, _payload("release"))), patch.object(
            config.requests, "get"
        ) as get:
            resolved = config._resolve_once()

        self.assertEqual(resolved["models"], config.WELFARE_MODELS)
        get.assert_not_called()

    def test_model_selection_switches_between_standard_and_boost(self):
        models = (config.WELFARE_STANDARD_MODEL, config.WELFARE_BOOST_MODEL)
        self.assertEqual(config.select_welfare_model(models, False), config.WELFARE_STANDARD_MODEL)
        self.assertEqual(config.select_welfare_model(models, True), config.WELFARE_BOOST_MODEL)

    def test_image_request_prefers_a_vision_capable_model(self):
        """带图请求优先选能看图的模型：否则图片会被不吃图的模型直接拒掉。"""
        models = (config.WELFARE_STANDARD_MODEL, config.WELFARE_BOOST_MODEL)

        # 没开智力提升、但这次带图：仍要选能看图的 2.5。
        self.assertEqual(
            config.select_welfare_model(models, False, needs_vision=True),
            config.WELFARE_BOOST_MODEL,
        )

    def test_image_request_uses_the_only_vision_model_even_if_it_is_not_preferred(self):
        self.assertEqual(
            config.select_welfare_model((config.WELFARE_BOOST_MODEL,), False, needs_vision=True),
            config.WELFARE_BOOST_MODEL,
        )

    def test_image_request_without_any_vision_model_reports_the_missing_preferred_one(self):
        """清单里一个能看图的都没有时，报的仍是原本要选的那个模型，便于排查。"""
        with self.assertRaisesRegex(RuntimeError, config.WELFARE_STANDARD_MODEL):
            config.select_welfare_model(("other-model",), False, needs_vision=True)

    def test_text_request_keeps_the_configured_model(self):
        models = (config.WELFARE_STANDARD_MODEL, config.WELFARE_BOOST_MODEL)

        self.assertEqual(
            config.select_welfare_model(models, False, needs_vision=False),
            config.WELFARE_STANDARD_MODEL,
        )

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
