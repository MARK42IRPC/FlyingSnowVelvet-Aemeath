from __future__ import annotations

import json
import threading
import time
import unittest
from unittest.mock import patch

from lib.script.chat.api_client_ollama import _ApiClientOllamaMixin
from lib.script.chat.api_client_openai import _ApiClientOpenAIMixin
from lib.script.chat.native_tools import (
    NativeToolCallAccumulator,
    get_native_tool_definitions,
    native_tool_to_dispatch,
)
from lib.script.chat.ollama_session import OllamaSessionMixin


class _StreamResponse:
    def __init__(self, chunks: list[dict]):
        self._lines = [json.dumps(chunk, ensure_ascii=False).encode("utf-8") for chunk in chunks]
        self.ok = True
        self.closed = False

    def iter_lines(self, **_kwargs):
        return iter(self._lines)

    def raise_for_status(self):
        return None

    def close(self):
        self.closed = True


class NativeToolCallingTests(unittest.TestCase):
    def test_openai_client_has_no_retired_yuanbao_runtime_dependency(self):
        self.assertFalse(hasattr(_ApiClientOpenAIMixin, "_refresh_yuanbao_runtime_config"))
        self.assertFalse(hasattr(_ApiClientOpenAIMixin, "_upload_yuanbao_multimedia"))

    def test_native_schema_uses_unique_ascii_function_names(self):
        tools = get_native_tool_definitions()
        names = [item["function"]["name"] for item in tools]

        self.assertEqual(len(names), len(set(names)))
        self.assertIn("play_music", names)
        self.assertIn("inspect_screen", names)
        self.assertTrue(all(name.isascii() for name in names))

    def test_openai_fragments_are_merged_into_one_validated_call(self):
        accumulator = NativeToolCallAccumulator()
        accumulator.consume_openai_chunk({
            "choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "function": {"name": "play_", "arguments": '{"query":"纸'},
            }]}}],
        })
        accumulator.consume_openai_chunk({
            "choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "function": {"name": "music", "arguments": '飞机"}'},
            }]}}],
        })

        self.assertEqual(
            accumulator.first(),
            {"name": "play_music", "arguments": {"query": "纸飞机"}},
        )

    def test_native_arguments_convert_to_existing_dispatch_contract(self):
        self.assertEqual(
            native_tool_to_dispatch({"name": "change_volume", "arguments": {"delta_percent": -10}}),
            ("音量", "-10"),
        )
        self.assertEqual(
            native_tool_to_dispatch({
                "name": "recall_memory",
                "arguments": {
                    "start_time": "2026-07-31 10:00:00",
                    "end_time": "2026-07-31 11:00:00",
                    "topic": "音乐",
                },
            }),
            ("回忆", "2026-07-31 10:00:00 到 2026-07-31 11:00:00 音乐"),
        )

    def test_openai_stream_returns_text_and_structured_call(self):
        response = _StreamResponse([
            {"choices": [{"delta": {"content": "这就播放。"}}]},
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "function": {"name": "play_music", "arguments": '{"query":"纸飞机"}'},
            }]}, "finish_reason": "tool_calls"}]},
        ])
        calls = []

        text = _ApiClientOpenAIMixin()._consume_openai_stream(
            response,
            on_chunk_emit=None,
            deadline=time.monotonic() + 5,
            on_tool_call=calls.append,
        )

        self.assertEqual(text, "这就播放。")
        self.assertEqual(calls, [{"name": "play_music", "arguments": {"query": "纸飞机"}}])

    def test_openai_payload_keeps_plain_gateway_fallback(self):
        payloads = [{
            "model": "test",
            "messages": [{"role": "system", "content": "persona"}],
            "stream": True,
        }]
        variants = _ApiClientOpenAIMixin._prepend_native_tool_payloads(payloads)

        self.assertIn("tools", variants[0])
        self.assertEqual(variants[0]["tool_choice"], "auto")
        self.assertIn("原生函数工具", variants[0]["messages"][0]["content"])
        self.assertNotIn("tools", variants[-1])
        self.assertEqual(variants[-1]["messages"][0]["content"], "persona")

    def test_ollama_chat_sends_tools_and_collects_native_call(self):
        response = _StreamResponse([
            {"message": {"content": "", "tool_calls": [{
                "function": {"name": "start_timer", "arguments": {"seconds": 45}},
            }]}, "done": True},
        ])
        calls = []

        with patch("lib.script.chat.api_client_ollama.requests.post", return_value=response) as post:
            text = _ApiClientOllamaMixin()._chat_api(
                "计时45秒",
                "persona",
                "test-model",
                on_tool_call=calls.append,
            )

        self.assertEqual(text, "")
        self.assertTrue(response.closed)
        self.assertIn("tools", post.call_args.kwargs["json"])
        self.assertEqual(calls, [{"name": "start_timer", "arguments": {"seconds": 45}}])

    def test_ollama_chat_omits_tools_when_disabled(self):
        response = _StreamResponse([{"message": {"content": "ok"}, "done": True}])

        with patch("lib.script.chat.api_client_ollama.requests.post", return_value=response) as post:
            _ApiClientOllamaMixin()._chat_api(
                "不要调用工具",
                "persona",
                "test-model",
                allow_tools=False,
            )

        self.assertNotIn("tools", post.call_args.kwargs["json"])

    def test_completion_callback_keeps_single_argument_compatibility(self):
        session = OllamaSessionMixin()
        session._chat_state_lock = threading.Lock()
        received = []
        session._chat_callbacks = {7: received.append}
        session._chat_chunk_callbacks = {}

        session._on_chat_ready(
            7,
            "完成",
            {"name": "next_track", "arguments": {}},
        )

        self.assertEqual(received, ["完成"])


if __name__ == "__main__":
    unittest.main()
