"""OpenAI-compatible stream parsing that must survive real gateway noise.

Uses the request library's line traversal contract directly: a fake response
implements ``iter_lines``, so no network or Qt is involved.
"""

from __future__ import annotations

import base64
import json
import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image
import requests

from lib.script.chat._api_client import _ApiClientMixin
from lib.script.chat import api_client_quick_welfare as welfare
from lib.script.chat import quick_welfare_api
from lib.script.chat import vision_codec


class _FakeResponse:
    def __init__(self, lines: list[str]):
        self._lines = [line.encode("utf-8") for line in lines]

    def iter_lines(self, *, chunk_size: int, decode_unicode: bool):
        del chunk_size, decode_unicode
        yield from self._lines

    def close(self) -> None:
        pass


def _chunk(text: str, *, reasoning: str = "") -> str:
    delta: dict[str, str] = {}
    if reasoning:
        delta["reasoning_content"] = reasoning
    if text:
        delta["content"] = text
    return json.dumps({"choices": [{"delta": delta}]})


class StreamParsingTests(unittest.TestCase):
    def setUp(self):
        self.client = _ApiClientMixin()

    def _consume(self, lines: list[str]) -> str:
        return self.client._consume_openai_stream(
            _FakeResponse(lines),
            on_chunk_emit=None,
            deadline=float("inf"),
        )

    def test_sse_comment_lines_do_not_swallow_following_chunks(self):
        # Kilo 网关在正式 chunk 前先发一行 SSE 注释。
        text = self._consume(
            [
                ": KILO PROCESSING",
                _chunk("你好"),
                ": keep-alive",
                _chunk("，漂泊者"),
                "data: " + _chunk("。"),
                "data: [DONE]",
            ]
        )
        self.assertEqual(text, "你好，漂泊者。")
        self.assertFalse(self.client._last_stream_reasoning_only)
        self.assertTrue(self.client._last_stream_text_seen)

    def test_reasoning_only_stream_is_flagged_for_model_rotation(self):
        text = self._consume(
            [
                _chunk("", reasoning="先想一想"),
                _chunk("", reasoning="再想一想"),
            ]
        )
        self.assertEqual(text, "")
        self.assertTrue(self.client._last_stream_reasoning_only)
        self.assertFalse(self.client._last_stream_text_seen)

    def test_multiline_sse_data_still_parses(self):
        payload = _chunk("ok")
        head, tail = payload[:10], payload[10:]
        text = self._consume([f"data: {head}", f"data: {tail}"])
        self.assertEqual(text, "ok")


class _FakeRouter:
    def __init__(self, models: list[str]):
        self._models = models
        self.failures: list[str] = []
        self.successes: list[str] = []

    def turn_models(self, limit, *, needs_vision=False):
        del needs_vision
        return self._models[:limit]

    def report_failure(self, model):
        self.failures.append(model)

    def report_success(self, model):
        self.successes.append(model)


class QuickWelfareRotationTests(unittest.TestCase):
    def test_empty_reply_rotates_to_the_next_model_instead_of_succeeding(self):
        router = _FakeRouter(["empty-model", "good-model"])
        calls: list[str] = []

        def fake_openai(self, message, persona, *, config_override=None, **kwargs):
            del message, persona, kwargs
            calls.append(config_override["model"])
            if config_override["model"] == "empty-model":
                self._last_stream_reasoning_only = False
                self._last_stream_text_seen = False
                return ""
            self._last_stream_reasoning_only = False
            self._last_stream_text_seen = True
            return "好"

        client = _ApiClientMixin()
        with patch.object(welfare, "get_quick_welfare_router", return_value=router), patch.object(
            welfare, "quick_welfare_config", side_effect=lambda model: {"model": model}
        ), patch.object(_ApiClientMixin, "_openai_chat_api", fake_openai):
            reply = client._quick_welfare_chat_api("hi", "persona")

        self.assertEqual(reply, "好")
        self.assertEqual(calls, ["empty-model", "good-model"])
        self.assertEqual(router.failures, ["empty-model"])
        self.assertEqual(router.successes, ["good-model"])

    def test_noisy_text_is_rotated_instead_of_being_reported_as_success(self):
        router = _FakeRouter(["noisy-model", "good-model"])
        calls: list[str] = []

        def fake_openai(self, message, persona, *, config_override=None, **kwargs):
            del message, persona, kwargs
            model = config_override["model"]
            calls.append(model)
            self._last_stream_reasoning_only = False
            self._last_stream_text_seen = True
            if model == "noisy-model":
                return "漂泊者没问题！正在给你找}$；$`(MPe6Jer)：`”；1wq=<8Cslu"
            return "好"

        client = _ApiClientMixin()
        with patch.object(welfare, "get_quick_welfare_router", return_value=router), patch.object(
            welfare, "quick_welfare_config", side_effect=lambda model: {"model": model}
        ), patch.object(_ApiClientMixin, "_openai_chat_api", fake_openai):
            reply = client._quick_welfare_chat_api("hi", "persona")

        self.assertEqual(reply, "好")
        self.assertEqual(calls, ["noisy-model", "good-model"])
        self.assertEqual(router.failures, ["noisy-model"])
        self.assertEqual(router.successes, ["good-model"])


class VisionPriorityTests(unittest.TestCase):
    def test_vision_priority_is_the_single_ordering_source(self):
        router = quick_welfare_api.QuickWelfareRouter()
        router._listing_fetched_at = float("inf")
        router._models = (
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "nvidia/nemotron-3.5-super-vl:free",
            "openrouter/free",
            "dots-studio/dots-3-note-preview:free",
        )
        router._vision_models = frozenset(router._models)

        self.assertEqual(
            router.turn_models(3, needs_vision=True),
            list(quick_welfare_api.QUICK_WELFARE_VISION_PRIORITY[:3]),
        )

        router._cursor = 0
        self.assertEqual(
            router.turn_models(4, needs_vision=True),
            list(quick_welfare_api.QUICK_WELFARE_VISION_PRIORITY),
        )


class QuickWelfareRequestOverrideTests(unittest.TestCase):
    def test_temperature_and_image_limit_ignore_display_settings(self):
        router = _FakeRouter(["vis-model"])
        captured: dict[str, object] = {}

        def fake_openai(self, message, persona, *, config_override=None, **kwargs):
            del message, persona, config_override
            captured.update(kwargs)
            self._last_stream_reasoning_only = False
            self._last_stream_text_seen = True
            return "好"

        client = _ApiClientMixin()
        with patch.object(welfare, "get_quick_welfare_router", return_value=router), patch.object(
            welfare, "quick_welfare_config", side_effect=lambda model: {"model": model}
        ), patch.object(_ApiClientMixin, "_openai_chat_api", fake_openai):
            client._quick_welfare_chat_api("看屏幕", "persona", images=[b"png"])

        self.assertEqual(captured["temperature_override"], 1.0)
        self.assertEqual(captured["image_max_size_override"], (1920, 1080))

    def test_temperature_and_image_limit_apply_to_text_only_retry(self):
        router = _FakeRouter(["no-vision-model"])
        captured: list[dict[str, object]] = []

        def fake_openai(self, message, persona, *, config_override=None, **kwargs):
            del message, persona, config_override
            captured.append(kwargs)
            self._last_stream_reasoning_only = False
            self._last_stream_text_seen = True
            if kwargs.get("images"):
                raise requests.HTTPError("400 image input not supported")
            return "好"

        client = _ApiClientMixin()
        with patch.object(welfare, "get_quick_welfare_router", return_value=router), patch.object(
            welfare, "quick_welfare_config", side_effect=lambda model: {"model": model}
        ), patch.object(_ApiClientMixin, "_openai_chat_api", fake_openai):
            client._quick_welfare_chat_api("看屏幕", "persona", images=[b"png"])

        self.assertEqual(len(captured), 2)
        for kwargs in captured:
            self.assertEqual(kwargs["temperature_override"], 1.0)
            self.assertEqual(kwargs["image_max_size_override"], (1920, 1080))


class VisionSizeTests(unittest.TestCase):
    def _png(self, width: int, height: int) -> bytes:
        output = BytesIO()
        Image.new("RGB", (width, height), (30, 60, 90)).save(output, format="PNG")
        return output.getvalue()

    def _sent_size(self, image_data: bytes, max_size) -> tuple[int, int]:
        block = vision_codec.images_to_openai_content([image_data], max_size=max_size)[0]
        url = block["image_url"]["url"]
        payload = base64.b64decode(url.split(",", 1)[1])
        with Image.open(BytesIO(payload)) as image:
            return image.size

    def test_landscape_screen_is_capped_at_1080p(self):
        size = self._sent_size(
            self._png(2560, 1440),
            quick_welfare_api.QUICK_WELFARE_VISION_MAX_SIZE,
        )
        self.assertEqual(size, (1920, 1080))

    def test_portrait_image_is_capped_by_the_same_1080p_boundary(self):
        size = self._sent_size(
            self._png(1440, 2560),
            quick_welfare_api.QUICK_WELFARE_VISION_MAX_SIZE,
        )
        self.assertEqual(size, (1080, 1920))

    def test_small_images_are_not_upscaled(self):
        size = self._sent_size(
            self._png(800, 600),
            quick_welfare_api.QUICK_WELFARE_VISION_MAX_SIZE,
        )
        self.assertEqual(size, (800, 600))


class FinalPayloadTests(unittest.TestCase):
    def test_quick_welfare_posts_temperature_one_and_1080p_image(self):
        output = BytesIO()
        Image.new("RGB", (2560, 1440), (30, 60, 90)).save(output, format="PNG")
        image_data = output.getvalue()
        sent: dict[str, object] = {}

        class _FakeHttpResponse:
            ok = True

            def raise_for_status(self):
                pass

            def iter_lines(self, *, chunk_size: int, decode_unicode: bool):
                del chunk_size, decode_unicode
                yield json.dumps({"choices": [{"delta": {"content": "好"}}]}).encode("utf-8")

            def close(self):
                pass

        def fake_request(self, method, endpoint, **kwargs):
            del self, method, endpoint
            sent.update(kwargs)
            return _FakeHttpResponse()

        client = _ApiClientMixin()
        config = quick_welfare_api.quick_welfare_config("vis-model")
        with patch.object(_ApiClientMixin, "_request_with_proxy_fallback", fake_request), patch.object(
            _ApiClientMixin, "_native_tools_available", return_value=False
        ), patch.object(_ApiClientMixin, "_supports_reasoning_extensions", return_value=False):
            reply = client._openai_chat_api(
                "看屏幕",
                "persona",
                images=[image_data],
                config_override=config,
                retry_attempts=1,
                max_output_tokens_override=quick_welfare_api.QUICK_WELFARE_MAX_OUTPUT_TOKENS,
                temperature_override=quick_welfare_api.QUICK_WELFARE_TEMPERATURE,
                image_max_size_override=quick_welfare_api.QUICK_WELFARE_VISION_MAX_SIZE,
            )

        self.assertEqual(reply, "好")
        payload = sent["json"]
        self.assertEqual(payload["temperature"], 1.0)
        image_url = payload["messages"][-1]["content"][1]["image_url"]["url"]
        image_bytes = base64.b64decode(image_url.split(",", 1)[1])
        with Image.open(BytesIO(image_bytes)) as image:
            self.assertEqual(image.size, (1920, 1080))


class DegenerateReplyTests(unittest.TestCase):
    def test_known_noise_shapes_are_rejected(self):
        for text in (
            "漂泊者没问题！正在给你找}$；$`(MPe6Jer)：`”；1wq=<8Cslu",
            "我看需要不需要我拿宠物给你看阿拉偕奴夢￣ε￣*uma昂nfat",
        ):
            with self.subTest(text=text):
                self.assertTrue(quick_welfare_api.is_degenerate_reply(text))

    def test_normal_replies_survive_the_filter(self):
        for text in (
            "你好呀，我在屏幕里看到你正在调试 Python 脚本，需要我帮你看日志吗？",
            "你在写 `asyncio.gather()`，这段代码会把多个请求并发发出去。",
            "The build passed at https://example.com/ci/123, but I saw two warnings.",
            "屏幕上方是一个终端窗口，下方是浏览器，标题栏写着「飞行雪绒」。",
            "好耶！(๑•̀ㅂ•́)و✧ 今天也要一起加油～",
            "我看到你在用VSCode写Python，终端里还有git status的输出。",
            "用AI看屏幕这件事，先确认一下屏幕内容。",
        ):
            with self.subTest(text=text):
                self.assertFalse(quick_welfare_api.is_degenerate_reply(text))


if __name__ == "__main__":
    unittest.main()
