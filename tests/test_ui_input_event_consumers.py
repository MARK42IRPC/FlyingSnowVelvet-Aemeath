import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lib.core.event.center import Event, EventCenter, EventType
from lib.core.render.visuals.types import Point
from lib.core.input.types import MouseButton
from lib.script.ui.close_button_handler import CloseButtonEventHandler
from lib.script.ui.command_dialog_handler import CommandDialogEventHandler
from lib.script.ui.restore_button import RestoreButton
from tests.timing_fakes import FakePump


class _QtPoint:
    def __init__(self, x, y):
        self._x = x
        self._y = y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _Rect:
    def __init__(self, width, height):
        self._width = width
        self._height = height

    def width(self):
        return self._width

    def height(self):
        return self._height

    def topLeft(self):
        return _QtPoint(0, 0)


class _Entry:
    def __init__(self):
        self.focused = False

    def setFocus(self):
        self.focused = True


class _UiTarget:
    def __init__(self, x, y, width, height):
        self._origin = _QtPoint(x, y)
        self._rect = _Rect(width, height)
        self.map_calls = 0

    def geometry(self):
        return self._rect

    def rect(self):
        return self._rect

    def mapToGlobal(self, _point):
        self.map_calls += 1
        return self._origin


class _Dialog(_UiTarget):
    def __init__(self, x, y, width, height):
        super().__init__(x, y, width, height)
        self._entry = _Entry()

    def isVisible(self):
        return True


class _EventSink:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class _CloseButton(_UiTarget):
    def __init__(self, x, y, width, height):
        super().__init__(x, y, width, height)
        self._visible = True
        self._anchor_available = True
        self._event_center = _EventSink()
        self.clicked = False

    def click(self):
        self.clicked = True


class UiInputEventConsumerTests(unittest.TestCase):
    def test_drag_press_with_right_click_ui_open_accepts_core_point(self):
        dialog_handler = CommandDialogEventHandler.__new__(CommandDialogEventHandler)
        dialog_handler._dialog = _Dialog(500, 500, 200, 80)
        close_handler = CloseButtonEventHandler.__new__(CloseButtonEventHandler)
        close_handler._button = _CloseButton(700, 500, 32, 32)

        center = EventCenter(pump_factory=lambda callback: FakePump(callback))
        center.subscribe(EventType.MOUSE_PRESS, dialog_handler._on_mouse_press)
        center.subscribe(EventType.MOUSE_PRESS, close_handler._on_mouse_press)

        with patch("lib.core.event.center.logger.exception") as log_exception:
            center.publish(Event(EventType.MOUSE_PRESS, {
                "button": MouseButton.LEFT,
                "global_pos": Point(120, 140),
            }))

        self.assertEqual(dialog_handler._dialog.map_calls, 1)
        self.assertEqual(close_handler._button.map_calls, 1)
        log_exception.assert_not_called()
        center.cleanup()

    def test_restore_button_converts_core_mouse_position_at_ui_boundary(self):
        probe = SimpleNamespace(_mouse_pos=None)

        RestoreButton._on_mouse_move(
            probe,
            Event(EventType.MOUSE_MOVE, {"global_pos": Point(12.4, 33.6)}),
        )

        self.assertEqual((probe._mouse_pos.x, probe._mouse_pos.y), (12, 34))

    def test_close_button_handler_reads_the_host_not_qt_parent(self):
        """控件迁出 QWidget 后 `_button.parent()` 不再存在。

        关闭按钮已是「描述 + 后端渲染」，它真正的窗口是 ``_runtime.host``。
        鼠标进入/离开若仍读 `parent()` 会抛 `AttributeError`，而事件中心把它吞成
        一条日志——表现只是"点击时按钮已经被销毁"，肉眼几乎看不出来。
        """
        class _Host:
            def __init__(self):
                self.fade_in = 0
                self.fade_out = 0

            def isVisible(self):
                return True

        class _Runtime:
            def __init__(self, host):
                self.host = host

        class _ProbeButton:
            def __init__(self, host):
                self._runtime = _Runtime(host)
                self.fades = []

            def fade_in(self):
                self.fades.append("in")

            def fade_out(self):
                self.fades.append("out")

        center = EventCenter(pump_factory=lambda callback: FakePump(callback))
        host = _Host()
        button = _ProbeButton(host)
        handler = CloseButtonEventHandler.__new__(CloseButtonEventHandler)
        handler._button = button
        handler._event_center = center

        with patch("lib.core.event.center.logger.exception") as log_exception:
            center.subscribe(EventType.MOUSE_ENTER, handler._on_mouse_enter)
            center.subscribe(EventType.MOUSE_LEAVE, handler._on_mouse_leave)
            center.publish(Event(EventType.MOUSE_ENTER, {"pet": host}))
            center.publish(Event(EventType.MOUSE_LEAVE, {"pet": host}))

        log_exception.assert_not_called()
        self.assertEqual(button.fades, ["in", "out"])
        center.cleanup()

    def test_input_event_handlers_do_not_import_pyqt(self):
        repo_root = Path(__file__).resolve().parents[1]
        for relative_path in (
            "lib/script/ui/command_dialog_handler.py",
            "lib/script/ui/close_button_handler.py",
        ):
            source = (repo_root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("PyQt5", source, relative_path)

    def test_event_handler_failure_log_identifies_event_and_callback(self):
        center = EventCenter(pump_factory=lambda callback: FakePump(callback))

        def broken_handler(_event):
            raise RuntimeError("broken")

        center.subscribe(EventType.INFORMATION, broken_handler)
        with patch("lib.core.event.center.logger.exception") as log_exception:
            center.publish(Event(EventType.INFORMATION))

        log_exception.assert_called_once()
        self.assertEqual(
            log_exception.call_args.args[0],
            "Event handler error: event=%s callback=%s",
        )
        self.assertEqual(log_exception.call_args.args[1], "information")
        self.assertTrue(
            log_exception.call_args.args[2].endswith(".broken_handler")
        )
        center.cleanup()


if __name__ == "__main__":
    unittest.main()
