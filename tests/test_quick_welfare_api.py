"""快捷福利 API：免费池清单、顺序兜底轮转与配置接线。

这条车道的价值全在「换模型」上，所以用例都打在轮转器与请求实现上，不碰真实网络：
清单拉取、单模型请求、失败分类都是注入或打桩的。
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

import config.ollama_config as ollama_config
from lib.script.chat import quick_welfare_api as qw
from lib.script.chat import api_client_quick_welfare as qw_client
from lib.script.chat.api_client_quick_welfare import QuickWelfareMixin


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    def close(self):
        pass


class QuickWelfareListingTests(unittest.TestCase):
    def test_listing_keeps_only_free_models_in_order(self):
        payload = {
            "data": [
                {"id": "paid/model", "isFree": False},
                {"id": "liquid/lfm-3-1.7b:free", "isFree": True},
                {"id": "  ", "isFree": True},
                {"id": "kilo-auto/free", "isFree": True},
                {"id": "liquid/lfm-3-1.7b:free", "isFree": True},
            ]
        }
        with patch.object(qw.requests, "get", return_value=_FakeResponse(payload)):
            models = qw.fetch_free_model_ids()

        self.assertEqual(models, ("liquid/lfm-3-1.7b:free", "kilo-auto/free"))

    def test_listing_without_free_models_is_an_error(self):
        payload = {"data": [{"id": "paid/model", "isFree": False}]}
        with patch.object(qw.requests, "get", return_value=_FakeResponse(payload)):
            self.assertEqual(qw.fetch_free_model_ids(), ())

    def test_listing_failure_keeps_the_fallback_roster(self):
        router = qw.QuickWelfareRouter()
        with patch.object(qw, "fetch_free_models", side_effect=RuntimeError("offline")):
            models = router.models()

        self.assertEqual(models, tuple(qw.QUICK_WELFARE_FALLBACK_MODELS))

    def test_listing_is_cached_between_calls(self):
        router = qw.QuickWelfareRouter()
        with patch.object(qw, "fetch_free_models", return_value=(("a:free", "b:free"), frozenset())) as fetch:
            router.models()
            router.models()

        self.assertEqual(fetch.call_count, 1)


class QuickWelfareRotationTests(unittest.TestCase):
    def _router(self, models=("a:free", "b:free", "c:free")):
        router = qw.QuickWelfareRouter()
        router._models = tuple(models)
        router._listing_fetched_at = float("inf")
        return router

    def test_turn_starts_at_the_next_model_each_time(self):
        router = self._router()

        first = router.turn_models(limit=1)
        second = router.turn_models(limit=1)
        third = router.turn_models(limit=1)

        self.assertEqual([first, second, third], [["a:free"], ["b:free"], ["c:free"]])

    def test_failed_model_goes_behind_healthy_ones(self):
        router = self._router()

        router.report_failure("a:free")
        order = router.turn_models(limit=3)

        self.assertEqual(order[:2], ["b:free", "c:free"])
        self.assertEqual(order[-1], "a:free")

    def test_success_moves_the_model_to_the_turn_start(self):
        router = self._router()

        router.report_success("c:free")

        self.assertEqual(router.turn_models(limit=3), ["c:free", "a:free", "b:free"])

    def test_success_clears_a_previous_cooldown(self):
        router = self._router()

        router.report_failure("a:free")
        self.assertGreater(router.cooldown_remaining("a:free"), 0)
        router.report_success("a:free")

        self.assertEqual(router.cooldown_remaining("a:free"), 0.0)

    def test_repeated_failures_get_the_long_cooldown(self):
        router = self._router()

        router.report_failure("a:free")
        short = router.cooldown_remaining("a:free")
        router.report_failure("a:free")
        long = router.cooldown_remaining("a:free")

        self.assertGreater(long, short)
        self.assertAlmostEqual(long, qw.QUICK_WELFARE_LONG_COOLDOWN_SECS, delta=1.0)

    def test_all_models_cooled_still_returns_a_roster(self):
        """全部模型都在冷却时也要照常发请求，而不是直接判死。"""
        router = self._router(("a:free",))

        router.report_failure("a:free")

        self.assertEqual(router.turn_models(limit=1), ["a:free"])

    def test_turn_limit_is_respected(self):
        router = self._router(tuple(f"m{index}:free" for index in range(10)))

        self.assertEqual(len(router.turn_models(limit=3)), 3)


class _StubQuickWelfare(QuickWelfareMixin):
    def __init__(self, outcomes, vision_models=()):
        self._outcomes = outcomes
        self.calls: list[tuple[str, bool]] = []
        self.output_budgets: list[int | None] = []
        self._router = qw.QuickWelfareRouter()
        self._router._models = tuple(f"m{index}:free" for index in range(len(outcomes)))
        self._router._vision_models = frozenset(vision_models)
        self._router._listing_fetched_at = float("inf")
        self._emitted: dict[str, bool] = {}

    def _extract_error(self, exc):
        return str(exc)

    def _openai_chat_api(self, message, persona, on_chunk_emit=None, images=None,
                         history=None, request_id=None, config_override=None,
                         allow_tools=True, on_tool_call=None,
                         retry_attempts=None, timeout_secs=None,
                         max_output_tokens_override=None):
        model = config_override["model"]
        # 免费池必须显式下发高输出预算：1024 会被思考吃光、正文为空。
        self.output_budgets.append(max_output_tokens_override)
        self.calls.append((model, images is not None))
        outcome = self._outcomes[int(model[1])]
        self._last_stream_reasoning_only = outcome == "reasoning-only"
        if outcome == "reasoning-only":
            return ""
        if outcome == "ok":
            if on_chunk_emit is not None:
                on_chunk_emit("正文")
            return "正文"
        if outcome == "ok-text-only":
            if images:
                raise _http_error(400, "image_url is not supported")
            if on_chunk_emit is not None:
                on_chunk_emit("正文")
            return "正文"
        if outcome == "fail-after-text":
            if on_chunk_emit is not None:
                on_chunk_emit("半截")
            raise RuntimeError(f"{model} failed after text")
        raise RuntimeError(f"{model} failed")


def _http_error(status, text):
    response = Mock()
    response.status_code = status
    response.text = text
    # `_extract_error` 由真实客户端实现，桩里直接把网关的原文当作错误文本，
    # `is_image_input_error()` 就是靠这段文本判定「模型不吃图片」的。
    return requests.HTTPError(f"{status} {text}", response=response)


class QuickWelfareTurnTests(unittest.TestCase):
    def _run(self, stub):
        with patch.object(qw_client, "get_quick_welfare_router", return_value=stub._router):
            return stub._quick_welfare_chat_api("hello", "persona")

    def test_failed_model_falls_through_to_the_next(self):
        stub = _StubQuickWelfare(["fail", "ok", "ok"])

        self.assertEqual(self._run(stub), "正文")
        self.assertEqual([model for model, _images in stub.calls], ["m0:free", "m1:free"])

    def test_every_turn_requests_the_large_free_tier_output_budget(self):
        """免费池不按 token 计费，必须显式要一份远大于付费默认的输出预算。

        默认的 1024 会被思考型模型的 reasoning 整个吃光：实测 Nemotron VL 在
        `finish_reason=length` 下 content 长度为 0，表现就是「只看到思考、没有正文」。
        """
        stub = _StubQuickWelfare(["ok"])

        self._run(stub)

        self.assertTrue(stub.output_budgets, "必须把预算传下去")
        self.assertEqual(
            set(stub.output_budgets),
            {qw.QUICK_WELFARE_MAX_OUTPUT_TOKENS},
        )
        self.assertGreater(qw.QUICK_WELFARE_MAX_OUTPUT_TOKENS, 4096)

    def test_turn_gives_up_after_the_configured_model_budget(self):
        stub = _StubQuickWelfare(["fail", "fail", "fail", "ok"])

        with self.assertRaises(RuntimeError):
            self._run(stub)
        self.assertEqual(len(stub.calls), qw.QUICK_WELFARE_MAX_MODELS_PER_TURN)

    def test_success_is_reported_to_the_router(self):
        stub = _StubQuickWelfare(["ok"])
        router = stub._router

        self._run(stub)

        self.assertEqual(router.cooldown_remaining("m0:free"), 0.0)

    def test_image_rejection_retries_text_on_the_same_model(self):
        stub = _StubQuickWelfare(["ok-text-only"])
        with patch.object(qw_client, "get_quick_welfare_router", return_value=stub._router):
            text = stub._quick_welfare_chat_api("hello", "persona", images=[b"png"])

        self.assertEqual(text, "正文")
        self.assertEqual(stub.calls, [("m0:free", True), ("m0:free", False)])

    def test_text_started_means_no_second_model_is_tried(self):
        """已经吐出正文后再换模型，会把半截回复接上别人的续写。"""
        stub = _StubQuickWelfare(["fail-after-text", "ok"])

        with self.assertRaises(RuntimeError):
            self._run(stub)
        self.assertEqual(len(stub.calls), 1)

    def test_image_turn_tries_vision_models_first(self):
        """带图时先试能看图的模型，别把一次超时浪费在纯文本模型上。"""
        stub = _StubQuickWelfare(["ok", "ok", "ok"], vision_models={"m2:free"})

        with patch.object(qw_client, "get_quick_welfare_router", return_value=stub._router):
            stub._quick_welfare_chat_api("hello", "persona", images=[b"png"])

        self.assertEqual(stub.calls[0][0], "m2:free")

    def test_text_turn_keeps_the_plain_rotation_order(self):
        stub = _StubQuickWelfare(["ok", "ok", "ok"], vision_models={"m2:free"})

        self._run(stub)

        self.assertEqual(stub.calls[0][0], "m0:free")

    def test_verified_vision_models_come_before_merely_declared_ones(self):
        """带图时先试实测能答的模型，只声明支持图片的排后面。

        实测（2026-11-11）dots / nemotron-super-vl 只吐 reasoning、inkling 持续 429，
        它们仍可用作纯文本，但不该占用带图请求的第一个名额。
        """
        stub = _StubQuickWelfare(
            ["ok", "ok", "ok", "ok"],
            vision_models={"m0:free", "m1:free", "m2:free"},
        )
        with patch.object(qw, "QUICK_WELFARE_VERIFIED_VISION_MODELS", frozenset({"m2:free"})):
            with patch.object(qw_client, "get_quick_welfare_router", return_value=stub._router):
                stub._quick_welfare_chat_api("hello", "persona", images=[b"png"])

        self.assertEqual(stub.calls[0][0], "m2:free")

    def test_image_rejection_moves_to_another_vision_model_before_dropping_the_image(self):
        """池里还有看图模型时，换它继续带图，而不是把图片丢掉只发文字。"""
        stub = _StubQuickWelfare(["ok-text-only", "ok"], vision_models={"m0:free", "m1:free"})

        with patch.object(qw_client, "get_quick_welfare_router", return_value=stub._router):
            text = stub._quick_welfare_chat_api("hello", "persona", images=[b"png"])

        self.assertEqual(text, "正文")
        self.assertEqual(stub.calls, [("m0:free", True), ("m1:free", True)])

    def test_image_falls_back_to_text_only_when_no_vision_model_is_left(self):
        stub = _StubQuickWelfare(["ok-text-only"], vision_models={"m0:free"})

        with patch.object(qw_client, "get_quick_welfare_router", return_value=stub._router):
            text = stub._quick_welfare_chat_api("hello", "persona", images=[b"png"])

        self.assertEqual(text, "正文")
        self.assertEqual(stub.calls, [("m0:free", True), ("m0:free", False)])

    def test_reasoning_only_model_is_replaced_by_the_next_one(self):
        """模型把预算全烧在思考上、正文为空时要换模型，不能当成成功返回空回复。

        回归自真实故障：Nemotron VL 系带图时 reasoning 吃掉全部 max_tokens，
        content 一个字符都没有，界面最终显示「当前模式不可用」。
        """
        stub = _StubQuickWelfare(["reasoning-only", "ok"])

        self.assertEqual(self._run(stub), "正文")
        self.assertEqual([model for model, _images in stub.calls], ["m0:free", "m1:free"])

    def test_all_reasoning_only_models_end_in_an_error(self):
        stub = _StubQuickWelfare(["reasoning-only", "reasoning-only", "reasoning-only"])

        with self.assertRaisesRegex(RuntimeError, "思考内容"):
            self._run(stub)

    def test_empty_pool_reports_a_clear_error(self):
        stub = _StubQuickWelfare([])
        stub._router._models = ()

        with self.assertRaisesRegex(RuntimeError, "没有可用的免费模型"):
            self._run(stub)


class QuickWelfareConfigTests(unittest.TestCase):
    def test_default_reply_mode_is_quick_welfare(self):
        self.assertEqual(ollama_config.get_ai_panel_setting_defaults()["force_reply_mode"], "5")

    def test_quick_welfare_config_carries_no_api_key(self):
        """网关见到非空 Bearer 会回 401，这条车道必须保持空密钥。"""
        config = qw.quick_welfare_config("liquid/lfm-3-1.7b:free")

        self.assertEqual(config["api_key"], "")
        self.assertEqual(config["base_url"], qw.QUICK_WELFARE_BASE_URL)
        self.assertEqual(config["key_source"], "quick_welfare")

    def test_active_config_routes_mode_five_to_the_free_pool(self):
        with patch.object(ollama_config, "FORCE_REPLY_MODE", "5"), patch.object(
            ollama_config, "_load_local_secret_overrides", return_value={}
        ):
            active = ollama_config.get_active_config()

        self.assertEqual(active["api_type"], "quick_welfare")
        self.assertEqual(actual_models := len(active["models"]), len(qw.QUICK_WELFARE_FALLBACK_MODELS))
        self.assertEqual(actual_models, 12)

    def test_office_mode_rejects_the_quick_lane(self):
        with patch.object(ollama_config, "FORCE_REPLY_MODE", "5"), patch.object(
            ollama_config, "OFFICE_MODE", {"use_independent_api": False}
        ), patch.object(ollama_config, "_load_local_secret_overrides", return_value={}):
            config = ollama_config.get_office_active_config()

        self.assertEqual(config["api_type"], "error")
        self.assertEqual(config["error"], ollama_config.OFFICE_API_PUBLIC_LANE_REJECTED)

    def test_stored_welfare_mode_is_migrated_to_quick_welfare(self):
        with patch.object(
            ollama_config, "get_section_overrides", return_value={"force_reply_mode": "1"}
        ), patch.object(
            ollama_config, "load_section", return_value={"force_reply_mode": "1"}
        ), patch.object(ollama_config, "save_section") as save:
            ollama_config._migrate_reply_mode_to_quick_welfare()

        save.assert_called_once()
        self.assertEqual(save.call_args.args[1]["force_reply_mode"], "5")

    def test_other_stored_modes_are_left_alone(self):
        for stored in ("0", "2", "3", "5"):
            with patch.object(
                ollama_config, "get_section_overrides", return_value={"force_reply_mode": stored}
            ), patch.object(ollama_config, "load_section") as load, patch.object(
                ollama_config, "save_section"
            ) as save:
                ollama_config._migrate_reply_mode_to_quick_welfare()

            save.assert_not_called()
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
