"""SEanima 特效处理。"""

from __future__ import annotations

from PIL import Image, ImageFilter

from config.config import ANIMATION

_EXIT_SHADOW_COLOR = (0, 0, 0)
_EXIT_SHADOW_DEFAULT_STRENGTH = 112
_EXIT_SHADOW_DEFAULT_BLUR_RADIUS = 14
_EXIT_SHADOW_DEFAULT_DIRECTION = "down"
_EXIT_SHADOW_MAX_STRENGTH = 255
_EXIT_SHADOW_MAX_BLUR_RADIUS = 128
_EXIT_SHADOW_OFFSET_DISTANCE_RATIO = 0.45
_EXIT_SHADOW_MIN_OFFSET_DISTANCE = 4
_EXIT_SHADOW_PADDING_MULTIPLIER = 2.2
_EXIT_SHADOW_DIRECTION_VECTORS = {
    "center": (0, 0),
    "down": (0, 1),
    "up": (0, -1),
    "left": (-1, 0),
    "right": (1, 0),
    "down_left": (-1, 1),
    "down_right": (1, 1),
    "up_left": (-1, -1),
    "up_right": (1, -1),
}
_EXIT_SHADOW_DIRECTION_ALIASES = {
    "": "down",
    "center": "center",
    "none": "center",
    "off": "center",
    "middle": "center",
    "stay": "center",
    "静止": "center",
    "无": "center",
    "不偏移": "center",
    "down": "down",
    "bottom": "down",
    "south": "down",
    "下": "down",
    "向下": "down",
    "up": "up",
    "top": "up",
    "north": "up",
    "上": "up",
    "向上": "up",
    "left": "left",
    "west": "left",
    "左": "left",
    "向左": "left",
    "right": "right",
    "east": "right",
    "右": "right",
    "向右": "right",
    "down_left": "down_left",
    "bottom_left": "down_left",
    "south_west": "down_left",
    "southwest": "down_left",
    "左下": "down_left",
    "向左下": "down_left",
    "down_right": "down_right",
    "bottom_right": "down_right",
    "south_east": "down_right",
    "southeast": "down_right",
    "右下": "down_right",
    "向右下": "down_right",
    "up_left": "up_left",
    "top_left": "up_left",
    "north_west": "up_left",
    "northwest": "up_left",
    "左上": "up_left",
    "向左上": "up_left",
    "up_right": "up_right",
    "top_right": "up_right",
    "north_east": "up_right",
    "northeast": "up_right",
    "右上": "up_right",
    "向右上": "up_right",
}


def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = int(default)
    return max(minimum, min(maximum, number))


def _normalize_exit_shadow_direction(value) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _EXIT_SHADOW_DIRECTION_ALIASES.get(key, _EXIT_SHADOW_DEFAULT_DIRECTION)


def _build_exit_shadow_metrics(width: int, height: int, strength: int, blur_radius: int, direction: str):
    strength = _clamp_int(strength, _EXIT_SHADOW_DEFAULT_STRENGTH, 0, _EXIT_SHADOW_MAX_STRENGTH)
    blur_radius = _clamp_int(
        blur_radius,
        _EXIT_SHADOW_DEFAULT_BLUR_RADIUS,
        0,
        min(_EXIT_SHADOW_MAX_BLUR_RADIUS, max(0, min(width, height))),
    )
    if strength <= 0:
        return None

    direction_key = _normalize_exit_shadow_direction(direction)
    vec_x, vec_y = _EXIT_SHADOW_DIRECTION_VECTORS.get(direction_key, _EXIT_SHADOW_DIRECTION_VECTORS["down"])
    offset_distance = 0
    if vec_x or vec_y:
        offset_distance = max(
            _EXIT_SHADOW_MIN_OFFSET_DISTANCE,
            int(round(max(1, blur_radius) * _EXIT_SHADOW_OFFSET_DISTANCE_RATIO)),
        )
    offset_x = vec_x * offset_distance
    offset_y = vec_y * offset_distance
    feather_pad = max(
        2,
        blur_radius + 2,
        int(round(max(1, blur_radius) * _EXIT_SHADOW_PADDING_MULTIPLIER)),
    )
    frame_x = feather_pad + max(0, -offset_x)
    frame_y = feather_pad + max(0, -offset_y)
    canvas_w = width + feather_pad * 2 + abs(offset_x)
    canvas_h = height + feather_pad * 2 + abs(offset_y)
    return {
        "strength": strength,
        "blur_radius": blur_radius,
        "direction": direction_key,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "frame_x": frame_x,
        "frame_y": frame_y,
        "canvas_w": canvas_w,
        "canvas_h": canvas_h,
        "alpha_lut": [(value * strength) // 255 for value in range(256)],
    }


def resolve_exit_shadow_metrics(width: int, height: int):
    animation_cfg = ANIMATION if isinstance(ANIMATION, dict) else {}
    return _build_exit_shadow_metrics(
        width,
        height,
        animation_cfg.get("exit_shadow_strength", _EXIT_SHADOW_DEFAULT_STRENGTH),
        animation_cfg.get("exit_shadow_blur_radius", _EXIT_SHADOW_DEFAULT_BLUR_RADIUS),
        animation_cfg.get("exit_shadow_offset_direction", _EXIT_SHADOW_DEFAULT_DIRECTION),
    )


def compose_exit_shadow_frame(frame: Image.Image, metrics) -> Image.Image:
    shadow_alpha = frame.getchannel("A").point(metrics["alpha_lut"])
    shadow = Image.new("RGBA", frame.size, _EXIT_SHADOW_COLOR + (0,))
    shadow.putalpha(shadow_alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=metrics["blur_radius"]))

    canvas = Image.new(
        "RGBA",
        (metrics["canvas_w"], metrics["canvas_h"]),
        (0, 0, 0, 0),
    )
    canvas.paste(
        shadow,
        (metrics["frame_x"] + metrics["offset_x"], metrics["frame_y"] + metrics["offset_y"]),
        shadow,
    )
    canvas.paste(frame, (metrics["frame_x"], metrics["frame_y"]), frame)
    return canvas
