"""Backend-neutral raster queries used by shared visual layers."""
from __future__ import annotations

from .resources import RasterFrame
from .types import Rect

#: Pixels at or below this alpha are treated as fully transparent padding.
ALPHA_THRESHOLD = 0


def opaque_bounds(frames: object) -> Rect | None:
    """Return the union of non-transparent pixels across ``frames``.

    This replaces toolkit-side alpha-mask unions: the crop decision is visual
    logic, so it belongs next to the other shared visuals instead of inside a
    QWidget or an image loader. Returns ``None`` when every frame is fully
    transparent, letting callers fall back to the full frame size.
    """
    resolved = tuple(frames) if not isinstance(frames, (bytes, bytearray)) else ()
    if not resolved:
        return None
    if any(not isinstance(frame, RasterFrame) for frame in resolved):
        raise TypeError("opaque bounds requires RasterFrame values")

    min_x = min_y = None
    max_x = max_y = -1
    for frame in resolved:
        pixels = frame.pixels
        width = frame.width
        stride = width * 4
        for row in range(frame.height):
            offset = row * stride
            # ``max`` over a stepped slice stays at C speed; only rows that
            # actually contain ink pay for the per-column scan below.
            alpha_row = pixels[offset:offset + stride:4]
            if max(alpha_row) <= ALPHA_THRESHOLD:
                continue
            row_min = next(
                column for column, alpha in enumerate(alpha_row) if alpha > ALPHA_THRESHOLD
            )
            row_max = width - 1 - next(
                column
                for column, alpha in enumerate(reversed(alpha_row))
                if alpha > ALPHA_THRESHOLD
            )
            if min_y is None:
                min_y = row
                min_x = row_min
            elif row_min < min_x:
                min_x = row_min
            if row_max > max_x:
                max_x = row_max
            max_y = row

    if min_y is None or min_x is None:
        return None
    return Rect(float(min_x), float(min_y), float(max_x - min_x + 1), float(max_y - min_y + 1))


__all__ = ["ALPHA_THRESHOLD", "opaque_bounds"]
