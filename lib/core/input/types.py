"""Qt-independent input event payloads."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag

from lib.core.graphics.types import Point


class MouseButton(IntEnum):
    NONE = 0
    LEFT = 1
    RIGHT = 2
    MIDDLE = 4


class MouseButtons(IntFlag):
    NONE = 0
    LEFT = MouseButton.LEFT
    RIGHT = MouseButton.RIGHT
    MIDDLE = MouseButton.MIDDLE


class Key(IntEnum):
    """Backend-neutral key codes used by core keyboard events."""

    UNKNOWN = 0
    SPACE = 32
    DIGIT_0 = 48
    DIGIT_1 = 49
    DIGIT_2 = 50
    DIGIT_3 = 51
    DIGIT_4 = 52
    DIGIT_5 = 53
    DIGIT_6 = 54
    DIGIT_7 = 55
    DIGIT_8 = 56
    DIGIT_9 = 57
    A = 65
    B = 66
    C = 67
    D = 68
    E = 69
    F = 70
    G = 71
    H = 72
    I = 73
    J = 74
    K = 75
    L = 76
    M = 77
    N = 78
    O = 79
    P = 80
    Q = 81
    R = 82
    S = 83
    T = 84
    U = 85
    V = 86
    W = 87
    X = 88
    Y = 89
    Z = 90
    ESCAPE = 0x01000000
    TAB = 0x01000001
    BACKSPACE = 0x01000003
    RETURN = 0x01000004
    ENTER = 0x01000005
    INSERT = 0x01000006
    DELETE = 0x01000007
    HOME = 0x01000010
    END = 0x01000011
    LEFT = 0x01000012
    UP = 0x01000013
    RIGHT = 0x01000014
    DOWN = 0x01000015
    PAGE_UP = 0x01000016
    PAGE_DOWN = 0x01000017
    F1 = 0x01000030
    F2 = 0x01000031
    F3 = 0x01000032
    F4 = 0x01000033
    F5 = 0x01000034
    F6 = 0x01000035
    F7 = 0x01000036
    F8 = 0x01000037
    F9 = 0x01000038
    F10 = 0x01000039
    F11 = 0x0100003A
    F12 = 0x0100003B
    F13 = 0x0100003C
    F14 = 0x0100003D
    F15 = 0x0100003E
    F16 = 0x0100003F
    F17 = 0x01000040
    F18 = 0x01000041
    F19 = 0x01000042
    F20 = 0x01000043
    F21 = 0x01000044
    F22 = 0x01000045
    F23 = 0x01000046
    F24 = 0x01000047


class KeyModifier(IntFlag):
    NONE = 0
    SHIFT = 0x02000000
    CONTROL = 0x04000000
    ALT = 0x08000000
    META = 0x10000000


@dataclass(frozen=True, slots=True)
class MouseInput:
    button: MouseButton = MouseButton.NONE
    buttons: MouseButtons = MouseButtons.NONE
    global_pos: Point = Point()
    pos: Point = Point()
    pet: object | None = None
    was_moving: bool = False


@dataclass(frozen=True, slots=True)
class KeyboardInput:
    key: Key | int = Key.UNKNOWN
    text: str = ""
    modifiers: KeyModifier = KeyModifier.NONE
    is_auto_repeat: bool = False
    pet: object | None = None
