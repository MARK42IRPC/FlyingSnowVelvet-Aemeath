import unittest
from unittest.mock import patch

from lib.core.event.center import Event, EventType
from lib.script.chat.handler import ChatHandler
from lib.script.tool_dispatcher.dispatcher import _SCREEN_PEEK_PROMPT
from lib.script.tool_dispatcher.dispatcher import ToolDispatcher


class _FakeEventCenter:
    def __init__(self):
        self.subscriptions = []
        self.published = []

    def subscribe(self, event_type, callback):
        self.subscriptions.append((event_type, callback))

    def unsubscribe(self, event_type, callback):
        self.subscriptions = [item for item in self.subscriptions if item != (event_type, callback)]

    def publish(self, event):
        self.published.append(event)


class _FakeOllama:
    def __init__(self):
        self.is_running = True
        self.calls = []

    def stream_chat(self, **kwargs):
        self.calls.append(kwargs)


class ScreenPeekToolTests(unittest.TestCase):
    def test_screen_peek_dispatches_one_input_chat(self):
        fake_center = _FakeEventCenter()
        with patch("lib.script.tool_dispatcher.dispatcher.get_event_center", return_value=fake_center):
            dispatcher = ToolDispatcher()
            dispatcher._on_stream_final(Event(EventType.STREAM_FINAL, {"text": "###窥屏###"}))
            dispatcher._on_stream_final(Event(EventType.STREAM_FINAL, {"text": "###窥屏###", "allow_tool_commands": False}))

        input_events = [event for event in fake_center.published if event.type == EventType.INPUT_CHAT]
        self.assertEqual(len(input_events), 1)
        self.assertEqual(input_events[0].data["source"], "tool_screen_peek")
        self.assertTrue(input_events[0].data["capture_screen"])
        self.assertFalse(input_events[0].data["allow_tool_commands"])

    def test_screen_peek_handler_sends_screenshot_once(self):
        fake_center = _FakeEventCenter()
        fake_ollama = _FakeOllama()
        with patch("lib.script.chat.handler.get_event_center", return_value=fake_center), patch(
            "lib.script.chat.handler.get_ollama_manager", return_value=fake_ollama
        ), patch("lib.script.chat.handler.capture_screen", return_value=[b"png-bytes"]):
            handler = ChatHandler()
            handler._on_input_chat(Event(EventType.INPUT_CHAT, {
                "text": _SCREEN_PEEK_PROMPT,
                "source": "tool_screen_peek",
                "capture_screen": True,
                "allow_tool_commands": False,
            }))

        self.assertEqual(len(fake_ollama.calls), 1)
        self.assertEqual(fake_ollama.calls[0]["images"], [b"png-bytes"])
        self.assertFalse(fake_ollama.calls[0]["allow_tools"])
        self.assertIn("当前主屏幕", fake_ollama.calls[0]["message"])
        fake_ollama.calls[0]["callback"]("ok")
        stream_events = [event for event in fake_center.published if event.type == EventType.STREAM_FINAL]
        self.assertTrue(stream_events)
        self.assertFalse(stream_events[-1].data["allow_tool_commands"])


if __name__ == "__main__":
    unittest.main()
