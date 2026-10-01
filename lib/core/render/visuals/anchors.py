"""Backend-neutral anchor geometry."""
from __future__ import annotations

from .types import Point, Rect


def get_anchor_point(rect: Rect, anchor_id: str) -> Point:
    """Return an anchor in rectangle-local coordinates."""
    x, y, width, height = rect.x, rect.y, rect.width, rect.height
    anchors = {
        "top": Point(x + width // 2, y),
        "bottom": Point(x + width // 2, y + height),
        "left": Point(x, y + height // 2),
        "right": Point(x + width, y + height // 2),
        "top_left": Point(x, y),
        "top_right": Point(x + width, y),
        "bottom_left": Point(x, y + height),
        "bottom_right": Point(x + width, y + height),
        "center": Point(x + width // 2, y + height // 2),
    }
    return anchors.get(anchor_id, anchors["center"])
