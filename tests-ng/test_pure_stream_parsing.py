"""OpenAI-compatible stream parsing that must survive real gateway noise.

Uses the request library's line traversal contract directly: a fake response
implements ``iter_lines``, so no network or Qt is involved.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from lib.script.chat._api_client import _ApiClientMixin
from lib.script.chat import api_client_quick_welfare as welfare


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


if __name__ == "__main__":
    unittest.main()
