"""Backend-neutral multi-screen geometry algorithms."""
from __future__ import annotations

from collections.abc import Iterable

from .types import Point, Rect


def virtual_screen_rect(rects: Iterable[Rect], fallback: Rect | None = None) -> Rect:
    """Return the bounding rectangle of all screens."""
    values = list(rects)
    if not values:
        return fallback or Rect(0, 0, 1920, 1080)

    left = min(rect.x for rect in values)
    top = min(rect.y for rect in values)
    right = max(rect.x + rect.width for rect in values)
    bottom = max(rect.y + rect.height for rect in values)
    return Rect(left, top, max(1, right - left), max(1, bottom - top))


def screen_for_point(point: Point | None, screens: Iterable[Rect], fallback: Rect) -> Rect:
    """Select the screen containing a point, or return the fallback screen."""
    values = list(screens)
    if point is None:
        return fallback
    for rect in values:
        if rect.x <= point.x < rect.x + rect.width and rect.y <= point.y < rect.y + rect.height:
            return rect
    return fallback


def clamp_rect_position(
    x: int,
    y: int,
    width: int,
    height: int,
    screen: Rect,
) -> tuple[int, int, Rect]:
    """Clamp a window top-left position to a screen rectangle."""
    min_x = screen.x
    min_y = screen.y
    max_x = screen.x + screen.width - width
    max_y = screen.y + screen.height - height
    if max_x < min_x:
        max_x = min_x
    if max_y < min_y:
        max_y = min_y
    cx = max(min_x, min(int(x), int(max_x)))
    cy = max(min_y, min(int(y), int(max_y)))
    return int(cx), int(cy), screen
