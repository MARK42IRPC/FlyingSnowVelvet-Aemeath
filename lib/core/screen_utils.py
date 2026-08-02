"""Legacy imports for Qt screen helpers now owned by ``qt_bridge``."""

from __future__ import annotations

def get_virtual_screen_geometry():
    from lib.core.qt_bridge.screen import get_virtual_screen_geometry as qt_geometry

    return qt_geometry()


def get_virtual_screen_rect():
    from lib.core.qt_bridge.screen import get_virtual_screen_rect as core_rect

    return core_rect()


def get_screen_geometry_for_point(
    point=None,
    fallback_widget=None,
):
    from lib.core.qt_bridge.screen import get_screen_geometry_for_point as qt_geometry

    return qt_geometry(point=point, fallback_widget=fallback_widget)


def get_screen_rect_for_point(point=None, fallback_widget=None):
    from lib.core.qt_bridge.screen import get_screen_rect_for_point as core_rect

    return core_rect(point=point, fallback_widget=fallback_widget)


def clamp_rect_position(
    x: int,
    y: int,
    width: int,
    height: int,
    point=None,
    fallback_widget=None,
):
    from lib.core.qt_bridge.screen import clamp_rect_position as qt_clamp_rect_position

    return qt_clamp_rect_position(
        x,
        y,
        width,
        height,
        point=point,
        fallback_widget=fallback_widget,
    )
