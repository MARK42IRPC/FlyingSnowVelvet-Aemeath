"""Qt-independent graphics contracts."""

from .backend import DrawBackend
from .anchors import get_anchor_point
from .capture import ScreenCapture
from .commands import DrawRequest, RenderItem, RenderRequest
from .collision import adjust_rect, point_in_rect, rects_intersect, segment_intersects_rect
from .scene import DrawScene
from .screen import clamp_rect_position, screen_for_point, virtual_screen_rect
from .types import Color, FontSpec, Point, Rect, Size, coerce_color, coerce_point, coerce_rect

__all__ = [
    "DrawRequest",
    "DrawBackend",
    "Color",
    "FontSpec",
    "get_anchor_point",
    "clamp_rect_position",
    "screen_for_point",
    "virtual_screen_rect",
    "DrawScene",
    "adjust_rect",
    "point_in_rect",
    "rects_intersect",
    "segment_intersects_rect",
    "Point",
    "Rect",
    "RenderItem",
    "RenderRequest",
    "ScreenCapture",
    "Size",
    "coerce_color",
    "coerce_point",
    "coerce_rect",
]
