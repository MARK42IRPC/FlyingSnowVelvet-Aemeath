from lib.core.graphics.types import Point
from lib.core.input.types import (
    Key,
    KeyboardInput,
    KeyModifier,
    MouseButton,
    MouseButtons,
    MouseInput,
)
from lib.core.qt_bridge.input import keyboard_input_from_qt, mouse_input_from_qt


class _MouseEvent:
    def button(self):
        return 1

    def buttons(self):
        return 1

    def globalPos(self):
        return _Point(100, 200)

    def pos(self):
        return _Point(10, 20)


class _KeyEvent:
    def key(self):
        return 32

    def text(self):
        return " "

    def modifiers(self):
        return 0

    def isAutoRepeat(self):
        return False


class _Point:
    def __init__(self, x, y):
        self._x = x
        self._y = y

    def x(self):
        return self._x

    def y(self):
        return self._y


def test_input_payloads_are_backend_neutral():
    mouse = MouseInput(
        button=MouseButton.LEFT,
        buttons=MouseButtons.LEFT,
        global_pos=Point(1, 2),
        pos=Point(3, 4),
    )
    key = KeyboardInput(key=Key.SPACE, text=" ")

    assert mouse.button == MouseButton.LEFT
    assert mouse.buttons & MouseButtons.LEFT
    assert key.key is Key.SPACE
    assert key.modifiers is KeyModifier.NONE


def test_qt_input_adapter_converts_raw_events():
    mouse = mouse_input_from_qt(_MouseEvent())
    key = keyboard_input_from_qt(_KeyEvent())

    assert mouse.button == MouseButton.LEFT
    assert mouse.buttons == MouseButtons.LEFT
    assert mouse.global_pos == Point(100, 200)
    assert mouse.pos == Point(10, 20)
    assert key == KeyboardInput(
        key=Key.SPACE,
        text=" ",
        modifiers=KeyModifier.NONE,
        is_auto_repeat=False,
    )
