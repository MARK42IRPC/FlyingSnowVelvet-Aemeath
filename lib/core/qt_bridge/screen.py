"""Qt screen queries converted to backend-neutral geometry."""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect
from PyQt5.QtWidgets import QApplication, QWidget

from lib.core.graphics.screen import clamp_rect_position as clamp_core_rect_position
from lib.core.graphics.types import Point, Rect, coerce_point


def _screens() -> list[object]:
    app = QApplication.instance()
    screens = list(app.screens()) if app is not None else []
    if screens:
        return screens
    primary = QApplication.primaryScreen()
    return [primary] if primary is not None else []


def get_virtual_screen_rect() -> Rect:
    """Return the union of all screens as a core Rect."""
    screens = _screens()
    if not screens:
        return Rect(0, 0, 1920, 1080)

    geometries = [screen.geometry() for screen in screens]
    left = min(geometry.x() for geometry in geometries)
    top = min(geometry.y() for geometry in geometries)
    right = max(geometry.x() + geometry.width() for geometry in geometries)
    bottom = max(geometry.y() + geometry.height() for geometry in geometries)
    return Rect(left, top, max(1, right - left), max(1, bottom - top))


def get_virtual_screen_geometry() -> QRect:
    """Return the virtual desktop as a Qt QRect for legacy UI callers."""
    rect = get_virtual_screen_rect()
    return QRect(int(rect.x), int(rect.y), int(rect.width), int(rect.height))


def _to_qpoint(value: QPoint | Point | object | None) -> QPoint | None:
    if value is None:
        return None
    if isinstance(value, QPoint):
        return value
    point = coerce_point(value)
    if point is None:
        return None
    return QPoint(int(round(point.x)), int(round(point.y)))


def get_screen_geometry_for_point(
    point: QPoint | Point | object | None = None,
    fallback_widget: QWidget | None = None,
) -> QRect:
    """Return the Qt screen geometry selected by a core or Qt point."""
    screen = None
    qt_point = _to_qpoint(point)
    if qt_point is not None:
        screen = QApplication.screenAt(qt_point)

    if screen is None and fallback_widget is not None:
        try:
            handle = fallback_widget.windowHandle()
        except Exception:
            handle = None
        if handle is not None:
            screen = handle.screen()
        if screen is None:
            try:
                screen = fallback_widget.screen()
            except Exception:
                screen = None

    if screen is None:
        screen = QApplication.primaryScreen()
    return screen.geometry() if screen is not None else get_virtual_screen_geometry()


def get_screen_rect_for_point(
    point: QPoint | Point | object | None = None,
    fallback_widget: QWidget | None = None,
) -> Rect:
    """Return the selected screen as backend-neutral geometry."""
    geometry = get_screen_geometry_for_point(
        point=point,
        fallback_widget=fallback_widget,
    )
    return Rect(
        geometry.x(),
        geometry.y(),
        geometry.width(),
        geometry.height(),
    )


def clamp_rect_position(
    x: int,
    y: int,
    width: int,
    height: int,
    point: QPoint | Point | object | None = None,
    fallback_widget: QWidget | None = None,
) -> tuple[int, int, QRect]:
    """Clamp a Qt window position to the selected screen."""
    geometry = get_screen_geometry_for_point(
        point=point,
        fallback_widget=fallback_widget,
    )
    clamped_x, clamped_y, _ = clamp_core_rect_position(
        x,
        y,
        width,
        height,
        Rect(
            geometry.x(),
            geometry.y(),
            geometry.width(),
            geometry.height(),
        ),
    )
    return clamped_x, clamped_y, geometry
