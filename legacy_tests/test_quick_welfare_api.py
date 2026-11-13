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

    def test_success_does_not_drag_a_weaker_model_to_the_front(self):
        """成功只清冷却，不改轮转起点——否则弱模型答成一次就长期霸占首位。"""
        router = self._router(("a:free", "b:free", "c:free"))

        router.report_success("c:free")

        # 起点仍在第一梯队内轮转，不会被 c 顶到最前。
        self.assertEqual(router.turn_models(limit=1), ["a:free"])

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
        self.reasoning_overrides: list[bool | None] = []
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
                         max_output_tokens_override=None,
                         reasoning_enabled_override=None, on_reasoning_emit=None):
        model = config_override["model"]
        # 免费池不截断输出：思考写完自然接正文，因此下发 0（= 不带 max_tokens）。
        self.output_budgets.append(max_output_tokens_override)
        self.reasoning_overrides.append(reasoning_enabled_override)
        self.calls.append((model, images is not None))
        outcome = self._outcomes[int(model[1])]
        self._last_stream_reasoning_only = outcome == "reasoning-only"
        if outcome == "reasoning-only":
            return ""
        if outcome == "transport-fail":
            raise requests.exceptions.ConnectionError("SSLError: EOF occurred in violation of protocol")
        if outcome == "quota":
            raise _http_error(429, '{"error":{"code":"PROMOTION_MODEL_LIMIT_REACHED"},'
                                   '"error_type":"rate_limit_exceeded"}')
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

    def test_every_turn_asks_for_an_uncapped_output_budget(self):
        """免费池不截断输出：预算传 0，请求里就不会带 `max_tokens`。

        截断没有收益，代价却是实打实的：思考型模型先写思考再写正文，预算在思考阶段
        耗尽时 `finish_reason=length`、正文长度为 0——用户看到的就是「只有思考、没有
        正文」。实测同一问题 1024 → 正文 0 字、16384 → 4127 字（仍被截断），不设上限
        才自然 `finish=stop`。
        """
        stub = _StubQuickWelfare(["ok"])

        self._run(stub)

        self.assertTrue(stub.output_budgets, "必须把预算传下去")
        self.assertEqual(set(stub.output_budgets), {0})
        self.assertEqual(qw.QUICK_WELFARE_MAX_OUTPUT_TOKENS, 0)

    def test_every_turn_forwards_the_thinking_switch_from_settings(self):
        """设置页「启用思考模式」必须对免费池生效。"""
        stub = _StubQuickWelfare(["ok"])

        with patch.object(qw_client, "quick_welfare_reasoning_enabled", return_value=False):
            self._run(stub)
        with patch.object(qw_client, "quick_welfare_reasoning_enabled", return_value=True):
            self._run(stub)

        self.assertEqual(stub.reasoning_overrides, [False, True])

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

        「实测能答」与「上游声明支持图片」是两回事：名单里的模型真的答过图，
        其余声明支持图片的模型只作后备。
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


class QuickWelfareTransportFailureTests(unittest.TestCase):
    """传输层失败必须立刻收手，不能在 payload 变体上反复重演。

    回归自真实日志：一次 TLS EOF 在 12 个 payload 变体上各撞一次、每次约 19 秒，
    单个免费模型耗掉 3 分半才轮到换模型，期间聊天队列一直被占着（自动陪伴只能
    「跳过：当前有聊天请求进行中」）。换请求体形状对连接层故障没有任何帮助。
    """

    _SSL_ERROR = (
        "HTTPSConnectionPool(host='ai-gateway.kilo.ai', port=443): Max retries exceeded "
        "with url: /api/v1/chat/completions (Caused by SSLError(SSLEOFError(8, "
        "'EOF occurred in violation of protocol (_ssl.c:2427)')))"
    )

    def _client(self):
        from lib.script.chat.api_client_openai import _ApiClientOpenAIMixin

        client = _ApiClientOpenAIMixin()
        client._active_config = {
            "base_url": "https://fake.example/api/v1",
            "api_key": "",
            "model": "m1",
        }
        return client

    def test_tls_eof_aborts_the_whole_target_after_one_attempt(self):
        calls = []

        def fail(_method, url, **_kwargs):
            calls.append(url)
            raise requests.exceptions.ConnectionError(self._SSL_ERROR)

        with patch.object(
            type(self._client()), "_request_with_proxy_fallback", side_effect=fail
        ):
            with self.assertRaises(requests.exceptions.ConnectionError):
                self._client()._openai_chat_api(
                    "hi", "persona", images=[b"png"], retry_attempts=3
                )

        self.assertEqual(len(calls), 1, "传输层失败只该发一次请求，而不是每个变体各一次")

    def test_transport_failure_classification(self):
        from lib.script.chat.api_client_openai import _is_transport_failure

        self.assertTrue(_is_transport_failure(
            requests.exceptions.SSLError("EOF occurred in violation of protocol")
        ))
        self.assertTrue(_is_transport_failure(
            requests.exceptions.ReadTimeout("read timed out")
        ))
        self.assertTrue(_is_transport_failure(
            requests.exceptions.ConnectionError(self._SSL_ERROR)
        ))
        self.assertTrue(_is_transport_failure(
            requests.exceptions.ConnectionError("Connection reset by peer")
        ))
        # 连接超时更像某一跳暂时不可达，重试一次仍有意义；HTTP 错误与传输无关。
        self.assertFalse(_is_transport_failure(
            requests.exceptions.ConnectTimeout("connect timed out")
        ))
        self.assertFalse(_is_transport_failure(
            requests.exceptions.HTTPError("400 Bad Request")
        ))

    def test_turn_switches_model_immediately_after_a_transport_failure(self):
        """免费池车道：传输层失败当场换模型，不重试同一个目标。"""
        stub = _StubQuickWelfare(["transport-fail", "ok"])

        self.assertEqual(self._run(stub), "正文")
        self.assertEqual([model for model, _images in stub.calls], ["m0:free", "m1:free"])

    def _run(self, stub):
        with patch.object(qw_client, "get_quick_welfare_router", return_value=stub._router):
            return stub._quick_welfare_chat_api("hello", "persona")


class QuickWelfareIntelligenceOrderTests(unittest.TestCase):
    """轮转顺序按智力排序，而不是按上游 `/models` 的注册顺序。"""

    def test_online_listing_is_reordered_by_intelligence(self):
        """线上清单只排序、不增删；未知模型排在已知模型之后。"""
        listing = (
            "liquid/lfm-3-1.7b:free",
            "brand-new/model:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "openrouter/free",
        )

        ordered = qw.sort_by_intelligence(listing)

        self.assertEqual(ordered[:2], (
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "liquid/lfm-3-1.7b:free",   # 已知但很弱，仍在未知模型之前
        ))
        # 未知模型排在最后（还没实测过，不该抢在前面），但不会被丢掉。
        self.assertEqual(ordered[-2:], ("openrouter/free", "brand-new/model:free"))
        self.assertEqual(sorted(ordered), sorted(listing))

    def test_fallback_roster_is_the_intelligence_order(self):
        self.assertEqual(
            tuple(qw.QUICK_WELFARE_FALLBACK_MODELS),
            tuple(qw.QUICK_WELFARE_INTELLIGENCE_ORDER),
        )
        self.assertEqual(
            qw.QUICK_WELFARE_INTELLIGENCE_ORDER[0],
            "nvidia/nemotron-3-ultra-550b-a55b:free",
        )
        # 不受控的两档必须排在最后。
        self.assertEqual(
            set(qw.QUICK_WELFARE_INTELLIGENCE_ORDER[-2:]),
            {"kilo-auto/free", "openrouter/free"},
        )
        self.assertEqual(len(set(qw.QUICK_WELFARE_INTELLIGENCE_ORDER)),
                         len(qw.QUICK_WELFARE_INTELLIGENCE_ORDER))

    def test_turn_never_starts_from_outside_the_preferred_tier(self):
        """轮转起点只在前三名内移动：弱模型不该被轮到首位。"""
        router = qw.QuickWelfareRouter()
        router._models = tuple(qw.QUICK_WELFARE_INTELLIGENCE_ORDER)
        router._listing_fetched_at = float("inf")

        starts = {router.turn_models(limit=1)[0] for _ in range(12)}

        self.assertEqual(
            starts,
            set(qw.QUICK_WELFARE_INTELLIGENCE_ORDER[: qw._PREFERRED_TIER_SIZE]),
        )

    def test_turn_order_keeps_the_rest_of_the_roster_behind_the_tier(self):
        router = qw.QuickWelfareRouter()
        router._models = tuple(qw.QUICK_WELFARE_INTELLIGENCE_ORDER)
        router._listing_fetched_at = float("inf")

        order = router.turn_models(limit=len(qw.QUICK_WELFARE_INTELLIGENCE_ORDER))
        tier = qw._PREFERRED_TIER_SIZE

        self.assertEqual(set(order[:tier]), set(qw.QUICK_WELFARE_INTELLIGENCE_ORDER[:tier]))
        self.assertEqual(order[tier:], list(qw.QUICK_WELFARE_INTELLIGENCE_ORDER[tier:]))


class QuickWelfareQuotaTests(unittest.TestCase):
    """免费池配额耗尽必须整轮收手，而不是把同一个 429 在每个模型上撞一遍。

    实测（2026-11-12）：一次 11 模型 × 13 题的并发评测就把池子打满，之后所有模型
    持续回 `429 PROMOTION_MODEL_LIMIT_REACHED`。这是 IP 级限制，换模型毫无意义，
    却会让界面等满三个超时才报「三个模型都失败了」。
    """

    _QUOTA = (
        '{"error":{"code":"PROMOTION_MODEL_LIMIT_REACHED","message":"Sign up for free to '
        'continue and explore 500 other models."},"error_type":"rate_limit_exceeded"}'
    )

    def test_quota_marker_is_recognised(self):
        from lib.script.chat.quick_welfare_api import is_quota_exhausted

        self.assertTrue(is_quota_exhausted(self._QUOTA))
        self.assertTrue(is_quota_exhausted("rate_limit_exceeded"))
        # 单个模型 400 / 图片不支持这类错误不该被当成配额问题。
        self.assertFalse(is_quota_exhausted("image_url is not supported"))
        self.assertFalse(is_quota_exhausted("Reasoning is mandatory"))
        self.assertFalse(is_quota_exhausted(""))

    def test_quota_exhaustion_stops_the_whole_turn(self):
        stub = _StubQuickWelfare(["quota"] * 4)
        router = stub._router

        with patch.object(qw_client, "get_quick_welfare_router", return_value=router):
            with self.assertRaisesRegex(RuntimeError, "免费额度已用完"):
                stub._quick_welfare_chat_api("hello", "persona")

        self.assertEqual(len(stub.calls), 1, "配额耗尽只该发一次请求，不能逐模型各撞一次")


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
        """模式 5 必须落到免费池，且模型清单与轮转器当前清单一致。

        这里不能断言「等于内置兜底清单」：轮转器会按 6 小时 TTL 拉一次线上清单，
        上游上下架模型后条数本来就会变（实测线上 11 个、内置兜底 12 个）。把清单
        拉取打桩成固定值，断言的是「配置取自轮转器」这条接线，而不是某个时刻的条数。
        """
        router = qw.get_quick_welfare_router()
        router.reset()
        with patch.object(
            qw, "fetch_free_models", return_value=(("a:free", "b:free"), frozenset())
        ), patch.object(ollama_config, "FORCE_REPLY_MODE", "5"), patch.object(
            ollama_config, "_load_local_secret_overrides", return_value={}
        ):
            active = ollama_config.get_active_config()
        router.reset()

        self.assertEqual(active["api_type"], "quick_welfare")
        self.assertEqual(tuple(active["models"]), ("a:free", "b:free"))
        self.assertEqual(active["model"], "a:free")

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
