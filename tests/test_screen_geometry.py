from lib.core.graphics.screen import clamp_rect_position, screen_for_point, virtual_screen_rect
from lib.core.graphics.types import Point, Rect


def test_virtual_screen_rect_unions_negative_and_positive_monitors():
    result = virtual_screen_rect([Rect(-1920, 0, 1920, 1080), Rect(0, 0, 2560, 1440)])
    assert result == Rect(-1920, 0, 4480, 1440)


def test_screen_selection_and_clamping_use_core_rects():
    left = Rect(-1920, 0, 1920, 1080)
    right = Rect(0, 0, 2560, 1440)
    assert screen_for_point(Point(-100, 20), [left, right], right) == left
    assert screen_for_point(Point(5000, 20), [left, right], right) == right

    x, y, screen = clamp_rect_position(-5000, 1400, 800, 900, right)
    assert (x, y) == (0, 540)
    assert screen == right
