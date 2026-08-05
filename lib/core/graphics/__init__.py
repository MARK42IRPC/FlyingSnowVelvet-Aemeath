"""Qt-independent graphics contracts."""

from .backend import DrawBackend
from .anchors import get_anchor_point
from .capture import ScreenCapture
from .commands import DrawBatch, DrawRequest, ResourceRevision, SpriteCommand
from .collision import adjust_rect, point_in_rect, rects_intersect, segment_intersects_rect
from .image_loader import (
    decode_image_frames,
    load_image_resource,
    resize_image_resource,
    resize_image_resource_to_height,
    resize_image_resource_to_width,
)
from .ordering import order_render_values
from .resources import ImageResource, RasterFrame
from .scene import DrawScene
from .screen import clamp_rect_position, screen_for_point, virtual_screen_rect
from .types import Color, FontSpec, Point, Rect, Size, coerce_color, coerce_point, coerce_rect

__all__ = [
    "DrawRequest",
    "DrawBatch",
    "DrawBackend",
    "Color",
    "FontSpec",
    "get_anchor_point",
    "decode_image_frames",
    "load_image_resource",
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
    "ImageResource",
    "order_render_values",
    "RasterFrame",
    "resize_image_resource",
    "resize_image_resource_to_height",
    "resize_image_resource_to_width",
    "ResourceRevision",
    "SpriteCommand",
    "ScreenCapture",
    "Size",
    "coerce_color",
    "coerce_point",
    "coerce_rect",
]
