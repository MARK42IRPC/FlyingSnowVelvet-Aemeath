"""Backend-neutral rectangle and segment collision helpers."""
from __future__ import annotations

from lib.core.graphics.types import Point, Rect, coerce_point, coerce_rect


def rects_intersect(first: Rect | object, second: Rect | object) -> bool:
    a = coerce_rect(first)
    b = coerce_rect(second)
    if a is None or b is None or a.width <= 0 or a.height <= 0 or b.width <= 0 or b.height <= 0:
        return False
    return (
        a.x < b.x + b.width
        and b.x < a.x + a.width
        and a.y < b.y + b.height
        and b.y < a.y + a.height
    )


def adjust_rect(
    rect: Rect | object,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> Rect:
    value = coerce_rect(rect) or Rect()
    return Rect(
        value.x + left,
        value.y + top,
        max(0.0, value.width + right - left),
        max(0.0, value.height + bottom - top),
    )


def point_in_rect(point: Point | object, rect: Rect | object) -> bool:
    value = coerce_point(point)
    bounds = coerce_rect(rect)
    if value is None or bounds is None or bounds.width <= 0 or bounds.height <= 0:
        return False
    return (
        bounds.x <= value.x <= bounds.x + bounds.width
        and bounds.y <= value.y <= bounds.y + bounds.height
    )


def segment_intersects_rect(
    start: Point | object,
    end: Point | object,
    rect: Rect | object,
) -> bool:
    """Return whether a closed line segment touches or crosses a rectangle."""
    first = coerce_point(start)
    second = coerce_point(end)
    bounds = coerce_rect(rect)
    if first is None or second is None or bounds is None:
        return False
    if bounds.width <= 0 or bounds.height <= 0:
        return False
    if point_in_rect(first, bounds) or point_in_rect(second, bounds):
        return True

    dx = second.x - first.x
    dy = second.y - first.y
    lower = 0.0
    upper = 1.0
    constraints = (
        (-dx, first.x - bounds.x),
        (dx, bounds.x + bounds.width - first.x),
        (-dy, first.y - bounds.y),
        (dy, bounds.y + bounds.height - first.y),
    )
    for direction, distance in constraints:
        if direction == 0:
            if distance < 0:
                return False
            continue
        ratio = distance / direction
        if direction < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True
