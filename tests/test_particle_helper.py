"""点击粒子辅助：按钮映射与事件载荷（后端中立 + Qt 翻译落点）。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import QPoint, Qt

from lib.core.event.center import EventType
from lib.core.render.visuals.controls import BUTTON_LEFT, BUTTON_RIGHT
from lib.script.ui import _particle_helper
from lib.script.ui import render_bridge


class _FakeEvent:
    def __init__(self, button, pos):
        self._button = button
        self._pos = pos

    def button(self):
        return self._button

    def pos(self):
        return self._pos


class _FakeWidget:
    def __init__(self, origin):
        self._origin = origin

    def mapToGlobal(self, local):
        return QPoint(self._origin[0] + local.x(), self._origin[1] + local.y())


class _RecordingCenter:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class ParticleHelperTests(unittest.TestCase):
    def test_pointer_button_name_translates_qt_buttons(self):
        self.assertEqual(
            render_bridge.pointer_button_name(_FakeEvent(Qt.LeftButton, QPoint())),
            BUTTON_LEFT,
        )
        self.assertEqual(
            render_bridge.pointer_button_name(_FakeEvent(Qt.RightButton, QPoint())),
            BUTTON_RIGHT,
        )

    def test_publish_click_particle_at_sends_screen_point(self):
        center = _RecordingCenter()
        with patch.object(_particle_helper, "get_event_center", return_value=center):
            _particle_helper.publish_click_particle_at("click", 12, 34)
        self.assertEqual(len(center.events), 1)
        event = center.events[0]
        self.assertEqual(event.type, EventType.PARTICLE_REQUEST)
        self.assertEqual(event.data["particle_id"], "click")
        self.assertEqual(event.data["area_type"], "point")
        self.assertEqual(event.data["area_data"], (12, 34))

    def test_empty_particle_id_is_ignored(self):
        center = _RecordingCenter()
        with patch.object(_particle_helper, "get_event_center", return_value=center):
            _particle_helper.publish_click_particle_at("", 0, 0)
        self.assertEqual(center.events, [])

    def test_left_and_right_clicks_use_the_shared_particle_map(self):
        for button, expected in ((Qt.LeftButton, "click"), (Qt.RightButton, "pink_click")):
            with self.subTest(button=button):
                center = _RecordingCenter()
                widget = _FakeWidget((100, 200))
                event = _FakeEvent(button, QPoint(5, 7))
                with patch.object(_particle_helper, "get_event_center", return_value=center):
                    _particle_helper.publish_click_particle(widget, event)
                self.assertEqual(center.events[0].data["particle_id"], expected)
                self.assertEqual(center.events[0].data["area_data"], (105, 207))


if __name__ == "__main__":
    unittest.main()
