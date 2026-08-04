import unittest

from PyQt5.QtCore import QPoint

from lib.core.event.center import EventType
from lib.core.graphics.types import Point
from lib.core.qt_bridge.widget_anchors import (
    get_anchor_point,
    publish_widget_anchor_response,
)


class _Rect:
    def __init__(self, x, y, width, height):
        self._values = x, y, width, height

    def x(self):
        return self._values[0]

    def y(self):
        return self._values[1]

    def width(self):
        return self._values[2]

    def height(self):
        return self._values[3]


class _Widget:
    def rect(self):
        return _Rect(0, 0, 100, 60)

    def x(self):
        return 300

    def y(self):
        return 200

    def get_anchor_point(self, anchor_id):
        return get_anchor_point(self, anchor_id)


class _EventSink:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class QtWidgetAnchorTests(unittest.TestCase):
    def test_qt_adapter_converts_core_anchor_to_qpoint(self):
        widget = _Widget()

        self.assertEqual(get_anchor_point(widget, "center"), QPoint(50, 30))
        self.assertEqual(
            get_anchor_point(widget, "bottom_right"),
            QPoint(100, 60),
        )

    def test_anchor_response_uses_backend_neutral_point_payload(self):
        sink = _EventSink()

        publish_widget_anchor_response(
            sink,
            _Widget(),
            window_id="pet_window",
            anchor_id="top_right",
            ui_id="close_button",
        )

        self.assertEqual(len(sink.events), 1)
        event = sink.events[0]
        self.assertIs(event.type, EventType.UI_ANCHOR_RESPONSE)
        self.assertEqual(event.data["anchor_point"], Point(400, 200))


if __name__ == "__main__":
    unittest.main()
