"""Qt QWidget operations used by the desktop pet host."""
from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QPainter

from lib.core.graphics.types import Point, coerce_point


def coerce_qpoint(value: object) -> QPoint | None:
    """Convert a point-like value to QPoint when it is valid."""
    point = coerce_point(value)
    if point is None:
        return None
    return QPoint(int(round(point.x)), int(round(point.y)))


def to_qpoint(value: object) -> QPoint:
    """Convert a core or legacy point-like value at the Qt boundary."""
    point = coerce_qpoint(value)
    if point is None:
        raise TypeError(f"cannot convert to QPoint: {value!r}")
    return point


def move_widget(widget, position: Point | object) -> None:
    """Move a QWidget using a backend-neutral position."""
    widget.move(to_qpoint(position))


def set_pet_window_clickthrough(widget, enabled: bool) -> None:
    """Apply the native Qt flags used by the transparent pet window."""
    base_flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
    widget.hide()
    widget.setAttribute(Qt.WA_TransparentForMouseEvents, bool(enabled))
    if enabled:
        widget.setWindowFlags(base_flags)
    else:
        widget.setWindowFlags(base_flags | Qt.WindowSystemMenuHint)
    widget.show()


def render_draw_core(widget, draw_core) -> None:
    """Render DrawCore into a QWidget paint event."""
    painter = QPainter(widget)
    try:
        draw_core.render(painter, widget.rect())
    finally:
        if painter.isActive():
            painter.end()
