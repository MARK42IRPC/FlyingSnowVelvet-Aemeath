"""Backend-neutral screen queries configured by the desktop host."""

from __future__ import annotations

from lib.core.desktop_backend import (
    get_screen_for_point_provider,
    get_virtual_screen_provider,
)
from lib.core.graphics.types import Point, Rect, coerce_point


_DEFAULT_SCREEN = Rect(0, 0, 1920, 1080)


def get_virtual_screen_rect() -> Rect:
    provider = get_virtual_screen_provider()
    return provider() if provider is not None else _DEFAULT_SCREEN

def get_screen_rect_for_point(point: Point | object | None = None) -> Rect:
    provider = get_screen_for_point_provider()
    core_point = coerce_point(point)
    return provider(core_point) if provider is not None else get_virtual_screen_rect()
