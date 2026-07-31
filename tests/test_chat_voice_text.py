import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config.ollama_config import OLLAMA
from lib.core.event.center import EventType
from lib.script.chat.handler_stream_presenter import (
    ChatHandlerStreamPresenterMixin,
    _build_ai_voice_text,
    _detect_ai_voice_language,
    _is_non_ai_status_text,
    _should_emit_ai_voice,
)


class ChatVoiceTextTests(unittest.TestCase):
    def test_english_voice_text_is_kept_and_split_on_english_period(self):
        with patch.dict(OLLAMA, {"ai_voice_max_chars": 80}, clear=False):
            self.assertEqual(
                _build_ai_voice_text("Hello, this is an English reply."),
                "Hello, this is an English reply.",
            )
        with patch.dict(OLLAMA, {"ai_voice_max_chars": 20}, clear=False):
            self.assertEqual(
                _build_ai_voice_text("Hello there. This sentence should wait."),
                "Hello there.",
            )

    def test_voice_language_hint_covers_english_chinese_and_mixed_text(self):
        self.assertEqual(_detect_ai_voice_language("Hello world"), "en")
        self.assertEqual(_detect_ai_voice_language("你好，世界"), "zh")
        self.assertEqual(_detect_ai_voice_language("你好, hello"), "auto")

    def test_openai_and_strict_mode_errors_are_not_voiceable(self):
        error_texts = (
            "当前回复模式请求失败: connection refused",
            "OpenAI API 错误: invalid api key",
            "OpenAI request failed: gateway timeout",
            "Ollama 异常: service unavailable",
        )
        for text in error_texts:
            with self.subTest(text=text):
                self.assertTrue(_is_non_ai_status_text(text))
                self.assertFalse(_should_emit_ai_voice(text))

    def test_auto_response_does_not_publish_error_to_onnx(self):
        events = []
        presenter = SimpleNamespace(
            _event_center=SimpleNamespace(publish=events.append),
            _calc_stream_final_min_ticks=lambda _text: 2,
        )

        ChatHandlerStreamPresenterMixin._publish_auto_response(
            presenter,
            "当前回复模式请求失败: connection refused",
        )

        self.assertFalse(
            any(event.type is EventType.AI_VOICE_REQUEST for event in events)
        )
        self.assertFalse(any(event.type is EventType.STREAM_FINAL for event in events))

    def test_auto_response_forwards_english_with_language_hint(self):
        events = []
        presenter = SimpleNamespace(
            _event_center=SimpleNamespace(publish=events.append),
            _calc_stream_final_min_ticks=lambda _text: 2,
        )

        ChatHandlerStreamPresenterMixin._publish_auto_response(
            presenter,
            "Hello from Aemeath.",
        )

        voice_events = [
            event for event in events if event.type is EventType.AI_VOICE_REQUEST
        ]
        self.assertEqual(len(voice_events), 1)
        self.assertEqual(voice_events[0].data["text"], "Hello from Aemeath.")
        self.assertEqual(voice_events[0].data["text_lang"], "en")

    def test_auto_response_forwards_mixed_text_without_losing_english(self):
        events = []
        presenter = SimpleNamespace(
            _event_center=SimpleNamespace(publish=events.append),
            _calc_stream_final_min_ticks=lambda _text: 2,
        )

        ChatHandlerStreamPresenterMixin._publish_auto_response(
            presenter,
            "你好, Aemeath! 今天 is sunny.",
        )

        voice_events = [
            event for event in events if event.type is EventType.AI_VOICE_REQUEST
        ]
        self.assertEqual(len(voice_events), 1)
        self.assertEqual(
            voice_events[0].data["text"],
            "你好, Aemeath! 今天 is sunny.",
        )
        self.assertEqual(voice_events[0].data["text_lang"], "auto")


if __name__ == "__main__":
    unittest.main()
