"""Qt input event conversion into core input payloads."""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCursor

from lib.core.graphics.types import Point
from lib.core.input.types import (
    Key,
    KeyboardInput,
    KeyModifier,
    MouseButton,
    MouseButtons,
    MouseInput,
)


def _mouse_button(value: object) -> MouseButton:
    value = int(value)
    mapping = {
        int(Qt.LeftButton): MouseButton.LEFT,
        int(Qt.RightButton): MouseButton.RIGHT,
        int(Qt.MiddleButton): MouseButton.MIDDLE,
    }
    return mapping.get(value, MouseButton.NONE)


def _mouse_buttons(value: object) -> MouseButtons:
    value = int(value)
    result = MouseButtons.NONE
    for qt_value, button in (
        (Qt.LeftButton, MouseButtons.LEFT),
        (Qt.RightButton, MouseButtons.RIGHT),
        (Qt.MiddleButton, MouseButtons.MIDDLE),
    ):
        if value & int(qt_value):
            result |= button
    return result


def _point(value: object) -> Point:
    return Point(float(value.x()), float(value.y()))


def mouse_input_from_qt(event: object, pet: object | None = None) -> MouseInput:
    """Convert a QMouseEvent-like object into a core payload."""
    return MouseInput(
        button=_mouse_button(event.button()),
        buttons=_mouse_buttons(event.buttons()),
        global_pos=_point(event.globalPos()),
        pos=_point(event.pos()),
        pet=pet,
    )


def keyboard_input_from_qt(event: object, pet: object | None = None) -> KeyboardInput:
    """Convert a QKeyEvent-like object into core key enums."""
    native_key = int(event.key())
    try:
        key = Key(native_key)
    except ValueError:
        key = native_key
    modifier_mask = int(
        Qt.ShiftModifier
        | Qt.ControlModifier
        | Qt.AltModifier
        | Qt.MetaModifier
    )
    return KeyboardInput(
        key=key,
        text=str(event.text()),
        modifiers=KeyModifier(int(event.modifiers()) & modifier_mask),
        is_auto_repeat=bool(event.isAutoRepeat()),
        pet=pet,
    )


def get_cursor_position() -> Point:
    position = QCursor.pos()
    return Point(float(position.x()), float(position.y()))
