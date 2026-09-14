from __future__ import annotations

import json
import threading
import time
import unittest
from unittest.mock import patch

from lib.script.chat import native_tools as native_tools_module
from lib.script.chat.api_client_ollama import _ApiClientOllamaMixin
from lib.script.chat.api_client_openai import _ApiClientOpenAIMixin
from lib.script.chat.native_tools import (
    LEGACY_TOOL_SYSTEM_NOTE,
    NATIVE_TOOL_SYSTEM_NOTE,
    NativeToolCallAccumulator,
    add_legacy_tool_instruction,
    add_native_tool_instruction,
    get_native_tool_definitions,
    native_tool_to_dispatch,
    strip_legacy_tool_protocol,
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

    def test_legacy_fallback_instruction_replaces_the_persona_markers(self):
        messages = [{"role": "system", "content": "persona"}]

        injected = add_legacy_tool_instruction(messages)

        self.assertEqual(messages[0]["content"], "persona")
        self.assertTrue(injected[0]["content"].startswith("persona"))
        self.assertIn(LEGACY_TOOL_SYSTEM_NOTE, injected[0]["content"])
        self.assertIn("###音乐", injected[0]["content"])

    def test_legacy_persona_block_is_stripped_at_request_time(self):
        legacy = "\n".join((
            "你是爱弥斯。",
            "[输出格式]",
            "4. 如需调用工具：///主题///正文###指令 参数###",
            "5. 工具命令必须放在整句末尾，且每次最多一个工具命令。",
            "[工具清单]",
            "使用示例1：///日常///这就召唤雪豹！###雪豹 3###",
            "1. ###音乐 歌名###：召唤音响并播放音乐。",
            "[工具使用规则]",
            "1. 除“回忆”“窥屏”外，其他工具不得主动调用。",
        ))

        cleaned = strip_legacy_tool_protocol(legacy)

        self.assertNotIn("###", cleaned)
        self.assertNotIn("[工具清单]", cleaned)
        self.assertIn("你是爱弥斯。", cleaned)
        self.assertIn("除“回忆”“窥屏”外", cleaned)

    def test_legacy_stripper_keeps_markdown_headings_and_plain_text(self):
        text = "# 标题\n\n### 子标题\n\n普通段落。"

        self.assertEqual(strip_legacy_tool_protocol(text), text)

    def test_runtime_persona_drops_legacy_tool_markers(self):
        from types import SimpleNamespace

        from lib.script.chat.handler_persona import ChatHandlerPersonaMixin

        holder = SimpleNamespace(
            _persona="你是爱弥斯。\n1. ###音乐 歌名###：召唤音响。",
            _build_recent_memory_block=lambda: "",
        )

        runtime = ChatHandlerPersonaMixin._build_runtime_persona(holder)

        self.assertIn("你是爱弥斯。", runtime)
        self.assertNotIn("###", runtime)

    def test_native_instruction_keeps_messages_untouched(self):
        messages = [{"role": "system", "content": "persona"}]

        injected = add_native_tool_instruction(messages)

        self.assertEqual(messages, [{"role": "system", "content": "persona"}])
        self.assertIn("原生函数工具", injected[0]["content"])

    def test_native_instruction_concatenates_the_toolcall_prompt_file(self):
        """工具用法放在独立 txt 里、请求期拼接，人格词保持用户可编辑。"""
        messages = [{"role": "system", "content": "persona"}]

        injected = add_native_tool_instruction(messages)

        content = injected[0]["content"]
        self.assertTrue(content.startswith("persona"))
        self.assertIn(NATIVE_TOOL_SYSTEM_NOTE, content)
        self.assertIn("play_music(query)", content)
        self.assertNotIn("play_music", messages[0]["content"])

    def test_toolcall_prompt_file_is_optional(self):
        messages = [{"role": "system", "content": "persona"}]

        with patch.object(native_tools_module, 'TOOLCALL_PROMPT_RELATIVE_PATH',
                          'resc/__missing_toolcall__.txt'):
            injected = add_native_tool_instruction(messages)

        self.assertEqual(injected[0]["content"], f"persona\n\n{NATIVE_TOOL_SYSTEM_NOTE}")

    def test_openai_legacy_payloads_keep_the_plain_gateway_fallback(self):
        payloads = [{
            "model": "test",
            "messages": [{"role": "system", "content": "persona"}],
            "stream": True,
        }]

        variants = _ApiClientOpenAIMixin._append_legacy_tool_payloads(payloads)

        self.assertIn("###音乐", variants[0]["messages"][0]["content"])
        self.assertNotIn("tools", variants[0])
        self.assertEqual(variants[-1]["messages"][0]["content"], "persona")

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

    def test_ollama_chat_falls_back_to_the_text_protocol(self):
        response = _StreamResponse([{"message": {"content": "ok"}, "done": True}])

        with patch.object(
            _ApiClientOllamaMixin, "_native_tools_available", return_value=False
        ), patch("lib.script.chat.api_client_ollama.requests.post", return_value=response) as post:
            _ApiClientOllamaMixin()._chat_api("放首歌", "persona", "test-model")

        payload = post.call_args.kwargs["json"]
        self.assertNotIn("tools", payload)
        self.assertIn("###音乐", payload["messages"][0]["content"])

    def test_ollama_chat_omits_the_fallback_when_tools_are_disabled(self):
        response = _StreamResponse([{"message": {"content": "ok"}, "done": True}])

        with patch.object(
            _ApiClientOllamaMixin, "_native_tools_available", return_value=False
        ), patch("lib.script.chat.api_client_ollama.requests.post", return_value=response) as post:
            _ApiClientOllamaMixin()._chat_api(
                "放首歌", "persona", "test-model", allow_tools=False
            )

        self.assertEqual(post.call_args.kwargs["json"]["messages"][0]["content"], "persona")

    def test_ollama_generate_prompt_carries_the_text_protocol(self):
        response = _StreamResponse([{"response": "ok", "done": True}])

        with patch("lib.script.chat.api_client_ollama.requests.post", return_value=response) as post:
            _ApiClientOllamaMixin()._generate_api("放首歌", "你是爱弥斯。", "test-model")

        self.assertIn("###音乐", post.call_args.kwargs["json"]["prompt"])

    def test_ollama_generate_omits_the_protocol_when_tools_are_disabled(self):
        response = _StreamResponse([{"response": "ok", "done": True}])

        with patch("lib.script.chat.api_client_ollama.requests.post", return_value=response) as post:
            _ApiClientOllamaMixin()._generate_api(
                "放首歌", "你是爱弥斯。", "test-model", allow_tools=False
            )

        self.assertNotIn("###音乐", post.call_args.kwargs["json"]["prompt"])

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
