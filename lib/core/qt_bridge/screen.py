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


def widget_global_rect(widget: QWidget) -> QRect:
    """返回任意控件（含子控件）在屏幕坐标系中的矩形。"""
    try:
        origin = widget.mapToGlobal(QPoint(0, 0))
    except Exception:
        return QRect(widget.x(), widget.y(), widget.width(), widget.height())
    return QRect(origin.x(), origin.y(), widget.width(), widget.height())


def move_widget_to_global(widget: QWidget, x: int, y: int) -> None:
    """按屏幕坐标移动控件，宿主分层时换算成宿主本地坐标。

    右键 UI 合并为一层后，命令框与附属按钮都是宿主窗口的子控件，`move()` 使用的是
    父窗口本地坐标。这里统一接收全局目标位置，记录到 `_layer_global_pos`（宿主据此
    计算自己的几何与命中区域），再换算成本地坐标后移动；`Qt` 下相同坐标的 `move()`
    不会触发原生窗口操作，拖动时每个帧只有宿主窗口移动一次。
    """
    x = int(x)
    y = int(y)
    try:
        widget._layer_global_pos = (x, y)
    except Exception:
        pass
    host = getattr(widget, "_layer_host", None)
    if host is not None:
        try:
            origin = host.mapToGlobal(QPoint(0, 0))
        except Exception:
            origin = None
        if origin is not None:
            x -= origin.x()
            y -= origin.y()
    if widget.x() != x or widget.y() != y:
        widget.move(x, y)
