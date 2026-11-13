import unittest
from unittest.mock import Mock, patch

from lib.core.event.center import Event, EventType
from lib.script.chat.handler_auto_companion import ChatHandlerAutoCompanionMixin, _resolve_auto_companion_interval
from lib.script.chat.handler_stream_presenter import ChatHandlerStreamPresenterMixin


class _DummyEventCenter:
    def __init__(self):
        self.published = []

    def publish(self, event):
        self.published.append(event)


class _DummyHandler(ChatHandlerStreamPresenterMixin):
    def __init__(self):
        self._event_center = _DummyEventCenter()
        self.appended = []

    def _append_recent_context(self, role: str, text: str):
        self.appended.append((role, text))

    @staticmethod
    def _calc_stream_final_min_ticks(text: str) -> int:
        return 2


class ChatAutoCompanionContextTests(unittest.TestCase):
    def test_interval_resolution_clamps_to_one_through_twenty_minutes(self):
        self.assertEqual(_resolve_auto_companion_interval((1, 1)), (60000, 60000))
        self.assertEqual(_resolve_auto_companion_interval((1200000, 1200000)), (1200000, 1200000))
        self.assertEqual(_resolve_auto_companion_interval((-1, 99999999)), (60000, 1200000))

    def test_ai_config_update_reschedules_auto_companion(self):
        handler = ChatHandlerAutoCompanionMixin()
        handler._on_app_main = Mock()

        handler._on_auto_companion_config_updated(
            Event(EventType.CONFIG_UPDATED, {"source": "ai"})
        )
        handler._on_auto_companion_config_updated(
            Event(EventType.CONFIG_UPDATED, {"source": "general"})
        )

        handler._on_app_main.assert_called_once()

    def test_publish_auto_response_records_user_and_assistant_context(self):
        handler = _DummyHandler()
        memory = Mock()

        with patch("lib.script.chat.memory.get_stream_memory", return_value=memory):
            handler._publish_auto_response("收到", include_history=True, user_text="观察屏幕")

        self.assertEqual(handler.appended, [("user", "观察屏幕"), ("assistant", "收到")])
        memory.record_user_input.assert_called_once_with("观察屏幕")
        self.assertGreaterEqual(len(handler._event_center.published), 2)


if __name__ == "__main__":
    unittest.main()
