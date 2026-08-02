"""QWidget anchor helpers backed by core geometry calculations."""
from __future__ import annotations

from PyQt5.QtCore import QPoint

from lib.core.event.center import Event, EventType
from lib.core.graphics.anchors import get_anchor_point as get_rect_anchor_point
from lib.core.graphics.types import Point, Rect, coerce_point


def get_anchor_point(widget, anchor_id: str) -> QPoint:
    """Return an anchor in widget-local Qt coordinates."""
    rect = widget.rect()
    point = get_rect_anchor_point(
        Rect(rect.x(), rect.y(), rect.width(), rect.height()),
        anchor_id,
    )
    return QPoint(int(point.x), int(point.y))


def get_aligned_position(
    widget,
    target_window,
    target_anchor_id: str,
    self_anchor_id: str = "top_left",
    offset_x: int = 0,
    offset_y: int = 0,
) -> QPoint:
    """Calculate an aligned global Qt position without moving the widget."""
    target_anchor = target_window.get_anchor_point(target_anchor_id)
    target_global_x = target_window.x() + target_anchor.x()
    target_global_y = target_window.y() + target_anchor.y()
    self_anchor = get_anchor_point(widget, self_anchor_id)
    return QPoint(
        target_global_x - self_anchor.x() + offset_x,
        target_global_y - self_anchor.y() + offset_y,
    )


def align_to_anchor(
    widget,
    target_window,
    target_anchor_id: str,
    self_anchor_id: str = "top_left",
    offset_x: int = 0,
    offset_y: int = 0,
) -> None:
    """Align a widget anchor to another Qt window and move immediately."""
    pos = get_aligned_position(
        widget=widget,
        target_window=target_window,
        target_anchor_id=target_anchor_id,
        self_anchor_id=self_anchor_id,
        offset_x=offset_x,
        offset_y=offset_y,
    )
    widget.move(pos.x(), pos.y())


def align_to_point(
    widget,
    point: QPoint,
    self_anchor_id: str = "top_left",
    offset_x: int = 0,
    offset_y: int = 0,
) -> None:
    """Align a widget anchor to a global QPoint and move immediately."""
    self_anchor = get_anchor_point(widget, self_anchor_id)
    widget.move(
        point.x() - self_anchor.x() + offset_x,
        point.y() - self_anchor.y() + offset_y,
    )


def publish_widget_anchor_response(
    event_center,
    widget,
    *,
    window_id: str,
    anchor_id: str,
    ui_id: str,
) -> None:
    """Publish a backend-neutral global anchor point."""
    anchor_point = coerce_point(widget.get_anchor_point(anchor_id)) or Point()
    global_point = Point(widget.x() + anchor_point.x, widget.y() + anchor_point.y)
    event_center.publish(Event(EventType.UI_ANCHOR_RESPONSE, {
        "window_id": window_id,
        "anchor_id": anchor_id,
        "anchor_point": global_point,
        "ui_id": ui_id,
    }))
