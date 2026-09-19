"""Global UI drawing scale helpers."""
from __future__ import annotations

import re

_draw_scale: float = 1.0
_user_scale: float = 1.0  # 用户缩放变量，默认为1
_px_pattern = re.compile(r"(-?\d+)px")
_draw_scale_range = (0.5, 3.0)
_user_scale_range = (0.5, 2.0)  # 用户缩放范围


def set_draw_scale(scale: float) -> None:
    """Set global draw scale factor."""
    global _draw_scale
    value = _parse_scale(scale)
    min_scale, max_scale = _draw_scale_range
    value = max(min_scale, min(max_scale, value))
    _draw_scale = value


def set_user_scale(scale: float) -> None:
    """Set global user scale factor."""
    global _user_scale
    min_scale, max_scale = _user_scale_range
    _user_scale = max(min_scale, min(max_scale, float(scale)))


def get_user_scale() -> float:
    """Get global user scale factor."""
    return _user_scale


def adjust_user_scale(delta: float) -> float:
    """
    Adjust user scale by delta and return new value.

    Args:
        delta: Adjustment amount (e.g., 0.1 or -0.1)

    Returns:
        New user scale value
    """
    global _user_scale
    new_value = _user_scale + delta
    min_scale, max_scale = _user_scale_range
    _user_scale = max(min_scale, min(max_scale, new_value))
    return _user_scale


def _parse_scale(value) -> float:
    """Parse scale values like 1.5, '1.5', '1,5', '150%'."""
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 1.0

    text = str(value).strip()
    if not text:
        return 1.0

    is_percent = text.endswith("%")
    if is_percent:
        text = text[:-1].strip()
    text = text.replace(",", ".")

    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return 1.0

    if is_percent:
        parsed /= 100.0
    return parsed if parsed > 0 else 1.0


def get_draw_scale() -> float:
    """Get global draw scale factor."""
    return _draw_scale


def scale_px(value: float | int, min_abs: int | None = None) -> int:
    """Scale a pixel value and round to nearest integer.

    The final scale is the product of draw_scale and user_scale.
    """
    raw = float(value)
    if raw == 0:
        return 0

    scaled = int(round(raw * _draw_scale * _user_scale))
    if min_abs is not None and scaled == 0:
        return min_abs if raw > 0 else -min_abs
    return scaled


def scale_size(size: tuple[int, int]) -> tuple[int, int]:
    """Scale a (width, height) size tuple."""
    return scale_px(size[0], min_abs=1), scale_px(size[1], min_abs=1)


def scale_style_px(style: str) -> str:
    """Scale all `Npx` values inside a stylesheet string."""

    def _replace(match: re.Match[str]) -> str:
        return f"{scale_px(int(match.group(1)), min_abs=1)}px"

    return _px_pattern.sub(_replace, style)
