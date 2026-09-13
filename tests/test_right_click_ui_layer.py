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

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtWidgets import QApplication, QWidget

from lib.core.event.center import Event, EventType
from lib.core.graphics.types import Point
from lib.core.qt_bridge.screen import move_widget_to_global
from lib.core.unified_draw import get_layer_manager
from lib.script.ui import pet_window_ui
from lib.script.ui.right_click_ui_layer import RightClickUiLayer


class _FakeEventCenter:
    def __init__(self) -> None:
        self.unsubscribed: list[tuple[object, object]] = []

    def unsubscribe(self, event_type, callback) -> None:
        self.unsubscribed.append((event_type, callback))


class RightClickUiLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _member(self, width: int = 80, height: int = 32) -> QWidget:
        widget = QWidget()
        widget.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        widget.setFixedSize(width, height)
        return widget

    def test_adopt_reparents_unregisters_and_stops_frame_updates(self):
        widget = self._member()
        widget._event_center = _FakeEventCenter()
        widget._on_frame = lambda event: None
        layer = RightClickUiLayer()

        with patch.object(get_layer_manager(), "unregister") as unregister:
            adopted = layer.adopt(widget)

        self.assertEqual(adopted, [widget])
        self.assertIs(widget.parent(), layer)
        self.assertFalse(widget.isWindow())
        self.assertIs(widget._layer_host, layer)
        self.assertEqual(layer.members(), (widget,))
        unregister.assert_called_once_with(widget)
        self.assertEqual(
            widget._event_center.unsubscribed,
            [(EventType.FRAME, widget._on_frame)],
        )

    def test_adopt_keeps_member_order_and_skips_duplicates(self):
        first = self._member()
        second = self._member()
        layer = RightClickUiLayer()

        layer.adopt(first, None, second, first)

        self.assertEqual(layer.members(), (first, second))

    def test_geometry_and_mask_follow_visible_members(self):
        layer = RightClickUiLayer()
        first = self._member(80, 32)
        second = self._member(40, 20)
        layer.adopt(first, second)
        first.show()
        second.show()
        first._layer_global_pos = (100, 200)
        second._layer_global_pos = (120, 240)
        layer.show_layer()

        layer._on_frame()

        self.assertEqual(layer.geometry(), QRect(100, 200, 80, 60))
        self.assertEqual(first.pos(), QPoint(0, 0))
        self.assertEqual(second.pos(), QPoint(20, 40))
        self.assertEqual(layer.mask_region().boundingRect(), QRect(0, 0, 80, 60))

        second.hide()
        layer._on_frame()

        self.assertEqual(layer.geometry(), QRect(100, 200, 80, 32))
        self.assertEqual(layer.mask_region().boundingRect(), QRect(0, 0, 80, 32))

    def test_show_and_hide_layer_track_visibility(self):
        layer = RightClickUiLayer()
        member = self._member()
        layer.adopt(member)
        member._layer_global_pos = (10, 20)

        layer.show_layer()
        self.assertTrue(layer.is_layer_visible())
        self.assertTrue(layer.isVisible())

        layer.hide_layer()
        self.assertFalse(layer.is_layer_visible())
        self.assertFalse(layer.isVisible())

    def test_frame_updates_are_skipped_while_layer_is_hidden(self):
        layer = RightClickUiLayer()
        member = self._member()
        calls: list[int] = []
        member._update_position = lambda: calls.append(1)
        layer.adopt(member)

        layer._on_frame()
        self.assertEqual(calls, [])

        layer.show_layer()
        layer._on_frame()
        self.assertEqual(calls, [1])

    def test_clickthrough_toggle_applies_to_whole_layer(self):
        layer = RightClickUiLayer()
        self._member()

        layer._on_clickthrough_toggle(Event(EventType.UI_CLICKTHROUGH_TOGGLE, {"enabled": True}))
        self.assertTrue(layer.testAttribute(Qt.WA_TransparentForMouseEvents))

        layer._on_clickthrough_toggle(Event(EventType.UI_CLICKTHROUGH_TOGGLE, {"enabled": False}))
        self.assertFalse(layer.testAttribute(Qt.WA_TransparentForMouseEvents))


class _PetStub:
    def __init__(self, x: int, y: int) -> None:
        self._position = Point(x, y)

    def get_core_position(self) -> Point:
        return self._position


class PetWindowUiIntegrationTests(unittest.TestCase):
    """整组右键控件确实落在同一个顶层窗口里，并且随桌宠整体移动。"""

    _MEMBERS = (
        "_cmd",
        "_hint_box",
        "_close_btn",
        "_clickthrough_btn",
        "_scale_up_btn",
        "_scale_down_btn",
        "_launch_wuwa_btn",
        "_chat_mode_btn",
        "_interaction_mode_btn",
        "_more_functions_btn",
    )

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._owner = QWidget()
        self.ui = pet_window_ui.create_pet_window_ui(self._owner, on_close=lambda: None)
        self.layer = self.ui["_right_click_ui_layer"]

    def tearDown(self):
        self.layer.close_layer()
        self.layer.deleteLater()
        self.app.processEvents()

    @staticmethod
    def _global_pos(widget) -> QPoint:
        origin = widget.mapToGlobal(QPoint(0, 0))
        return QPoint(origin.x(), origin.y())

    def test_all_right_click_widgets_share_one_top_level_window(self):
        members = self.layer.members()

        self.assertEqual(len(members), len(self._MEMBERS) + 2)
        for name in self._MEMBERS:
            widget = self.ui[name]
            self.assertIs(widget.parent(), self.layer)
            self.assertFalse(widget.isWindow())
            self.assertTrue(widget in members)
        self.assertIs(self.ui["_cmd"]._layer_host, self.layer)

    def test_group_moves_as_one_window_with_the_pet(self):
        pet = _PetStub(600, 400)
        self.ui["_cmd"].toggle(pet)
        self.layer._on_frame()

        cmd = self.ui["_cmd"]
        self.assertTrue(self.layer.is_layer_visible())
        cmd_local = cmd.pos()
        layer_geometry = self.layer.geometry()
        self.assertFalse(self.layer.mask_region().isEmpty())
        before = {}
        for name in self._MEMBERS:
            widget = self.ui[name]
            position = self._global_pos(widget)
            self.assertTrue(layer_geometry.contains(QRect(position, widget.size())))
            before[name] = position

        move = QPoint(40, 25)
        pet._position = Point(600 + move.x(), 400 + move.y())
        event_center = self.ui["_cmd"]._event_center
        event_center.publish(Event(EventType.UI_ANCHOR_RESPONSE, {
            "window_id": "pet_window",
            "anchor_id": "all",
            "anchor_point": Point(pet._position.x, pet._position.y),
            "ui_id": "all",
        }))
        self.layer._on_frame()

        moved_geometry = self.layer.geometry()
        self.assertEqual(moved_geometry.topLeft(), layer_geometry.topLeft() + move)
        # 子控件在宿主里的本地坐标不变，整组随宿主窗口一次性移动。
        self.assertEqual(cmd.pos(), cmd_local)
        for name in self._MEMBERS:
            self.assertEqual(
                self._global_pos(self.ui[name]),
                before[name] + move,
                msg=name,
            )

    def test_hiding_group_hides_layer(self):
        pet = _PetStub(500, 300)
        cmd = self.ui["_cmd"]
        cmd.toggle(pet)
        self.layer._on_frame()

        cmd.toggle(pet)
        cmd._on_anim_finished()

        self.assertFalse(self.layer.is_layer_visible())
        self.assertFalse(self.layer.isVisible())


class MoveWidgetToGlobalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_move_without_host_uses_global_coordinates(self):
        widget = QWidget()
        widget.setFixedSize(10, 10)

        move_widget_to_global(widget, 30, 40)

        self.assertEqual(widget.pos(), QPoint(30, 40))
        self.assertEqual(widget._layer_global_pos, (30, 40))

    def test_move_with_host_translates_to_host_local_coordinates(self):
        layer = RightClickUiLayer()
        widget = QWidget()
        widget.setFixedSize(10, 10)
        layer.adopt(widget)
        layer.setGeometry(200, 100, 300, 300)

        move_widget_to_global(widget, 230, 140)

        self.assertEqual(widget.pos(), QPoint(30, 40))
        self.assertEqual(widget._layer_global_pos, (230, 140))


if __name__ == "__main__":
    unittest.main()
