import logging
import unittest
from unittest.mock import Mock

from lib.core.event.center import Event, EventType, cleanup_event_center, get_event_center
from lib.core.logger import _ErrorEventHandler
from lib.script.ui.bubble import Bubble


class ErrorVoiceLogTriggerTests(unittest.TestCase):
    def tearDown(self):
        cleanup_event_center()

    def test_error_log_records_publish_log_error_event(self):
        events = []
        center = get_event_center()
        center.subscribe(EventType.LOG_ERROR, events.append)

        handler = _ErrorEventHandler()
        handler.handle(logging.LogRecord("app.test", logging.INFO, __file__, 1, "daily error word", (), None))
        handler.handle(logging.LogRecord("app.test", logging.ERROR, __file__, 2, "boom", (), None))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data["levelno"], logging.ERROR)
        self.assertEqual(events[0].data["message"], "boom")

    def test_bubble_information_text_no_longer_triggers_bug_voice(self):
        bubble = Bubble.__new__(Bubble)
        bubble._bug_sound = Mock()
        bubble.add_bubble = Mock()

        Bubble._on_information(
            bubble,
            Event(EventType.INFORMATION, {"text": "日常台词里带错误两个字", "min": 1, "max": 2}),
        )

        bubble._bug_sound.play.assert_not_called()
        bubble.add_bubble.assert_called_once()

    def test_bubble_log_error_event_triggers_bug_voice(self):
        bubble = Bubble.__new__(Bubble)
        bubble._bug_sound = Mock()

        Bubble._on_log_error(bubble, Event(EventType.LOG_ERROR, {"levelno": logging.ERROR}))

        bubble._bug_sound.play.assert_called_once()


if __name__ == "__main__":
    unittest.main()
