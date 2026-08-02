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

from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QCloseEvent, QMoveEvent
from PyQt5.QtWidgets import QApplication

from lib.core.graphics.types import Point
from lib.core.input.types import KeyboardInput, Key, MouseButton, MouseInput
from lib.core.qt_bridge import pet_widget as pet_widget_module
from lib.core.qt_bridge.pet_widget import QtPetWidget


class _PetWidgetProbe(QtPetWidget):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.draw_core = object()

    def change_state(self, state):
        pass

    def get_current_state(self):
        return "idle"

    def start_move(self, target):
        pass

    def stop_move(self):
        pass

    def play_animation(self, state, duration=0):
        pass

    def spawn_particles(self, *args, **kwargs):
        pass

    def toggle_command_dialog(self):
        pass

    def schedule_task(self, callback, delay_ms, repeat=False):
        return "task"

    def cancel_task(self, task_id):
        pass

    def is_moving(self):
        return False

    def set_direction(self, flipped):
        pass

    def get_direction(self):
        return False

    def prepare_render(self):
        self.calls.append(("render",))
        return self.draw_core

    def handle_pointer_enter(self):
        self.calls.append(("enter",))

    def handle_pointer_leave(self):
        self.calls.append(("leave",))

    def handle_pointer_press(self, event):
        self.calls.append(("press", event))

    def handle_pointer_move(self, event):
        self.calls.append(("pointer_move", event))

    def handle_pointer_release(self, button):
        self.calls.append(("release", button))

    def handle_window_moved(self, position):
        self.calls.append(("window_move", position))

    def handle_key_press(self, event):
        self.calls.append(("key_press", event))

    def handle_key_release(self, event):
        self.calls.append(("key_release", event))

    def handle_host_close(self):
        self.calls.append(("close",))


class QtPetWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = _PetWidgetProbe()

    def tearDown(self):
        self.widget.deleteLater()
        self.app.processEvents()

    def test_render_and_pointer_events_delegate_to_core_callbacks(self):
        mouse_press = MouseInput(button=MouseButton.LEFT, global_pos=Point(4, 5))
        mouse_move = MouseInput(global_pos=Point(8, 9))
        mouse_release = MouseInput(button=MouseButton.RIGHT)

        with patch.object(pet_widget_module, "render_draw_core") as render, patch.object(
            pet_widget_module,
            "mouse_input_from_qt",
            side_effect=[mouse_press, mouse_move, mouse_release],
        ):
            self.widget.paintEvent(None)
            self.widget.enterEvent(None)
            self.widget.leaveEvent(None)
            self.widget.mousePressEvent(object())
            self.widget.mouseMoveEvent(object())
            self.widget.mouseReleaseEvent(object())

        render.assert_called_once_with(self.widget, self.widget.draw_core)
        self.assertEqual(
            self.widget.calls,
            [
                ("render",),
                ("enter",),
                ("leave",),
                ("press", mouse_press),
                ("pointer_move", mouse_move),
                ("release", MouseButton.RIGHT),
            ],
        )

    def test_key_move_and_close_events_delegate_to_core_callbacks(self):
        key_press = KeyboardInput(key=Key.A, text="a")
        key_release = KeyboardInput(key=Key.A)

        with patch.object(
            pet_widget_module,
            "keyboard_input_from_qt",
            side_effect=[key_press, key_release],
        ):
            self.widget.keyPressEvent(object())
            self.widget.keyReleaseEvent(object())

        self.widget.moveEvent(QMoveEvent(QPoint(12, 34), QPoint(1, 2)))
        self.widget.closeEvent(QCloseEvent())

        self.assertEqual(
            self.widget.calls,
            [
                ("key_press", key_press),
                ("key_release", key_release),
                ("window_move", Point(12, 34)),
                ("close",),
            ],
        )


if __name__ == "__main__":
    unittest.main()
