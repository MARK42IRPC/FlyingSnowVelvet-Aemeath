"""特效工具函数 - 提供便捷的特效事件发布接口。"""

from __future__ import annotations

from lib.core.event.center import Event, EventType, get_event_center


def _copy_effect_payload(value, *, field: str):
    """Copy backend-neutral effect data and reject opaque toolkit objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, tuple):
        return tuple(
            _copy_effect_payload(item, field=f"{field}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, list):
        return [
            _copy_effect_payload(item, field=f"{field}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        copied = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field} keys must be strings")
            copied[key] = _copy_effect_payload(item, field=f"{field}.{key}")
        return copied
    raise TypeError(f"{field} contains unsupported value: {type(value).__name__}")


def spawn_effect(
    effect_id: str,
    anchor_type: str = "point",
    anchor_data=None,
    effect_options: dict | None = None,
    *,
    z: int = 0,
):
    """发布通用特效申请事件。"""
    options = _copy_effect_payload(dict(effect_options or {}), field="effect_options")
    options.setdefault("z", int(z))
    event = Event(EventType.EFFECT_REQUEST, {
        "effect_id": effect_id,
        "anchor_type": anchor_type,
        "anchor_data": _copy_effect_payload(anchor_data, field="anchor_data"),
        "effect_options": options,
    })
    get_event_center().publish(event)


def spawn_smooth_image_effect(
    intro_start_pos,
    intro_duration: float,
    display_pos,
    display_duration: float,
    outro_end_pos,
    outro_duration: float,
    resource_path: str,
    scale: float = 1.0,
    *,
    z: int = 0,
    effect_options: dict | None = None,
):
    """
    发射丝滑图片展示特效。

    位置坐标均使用全局屏幕坐标，图片以传入位置作为中心点绘制。
    """
    options = {
        "intro_start_pos": intro_start_pos,
        "intro_duration": intro_duration,
        "display_pos": display_pos,
        "display_duration": display_duration,
        "outro_end_pos": outro_end_pos,
        "outro_duration": outro_duration,
        "resource_path": resource_path,
        "scale": scale,
    }
    options.update(dict(effect_options or {}))
    options.setdefault("z", int(z))

    spawn_effect(
        effect_id="smooth_image_show",
        anchor_type="point",
        anchor_data=display_pos,
        effect_options=options,
    )


def spawn_flash_text_effect(
    center_pos,
    *,
    text: str,
    fade_in_duration: float,
    fade_in_frequency: float,
    hold_duration: float,
    fade_out_duration: float,
    fade_out_frequency: float,
    font_type: str = "ui",
    font_size: int = 32,
    color=(255, 255, 255),
    bold: bool = False,
    font_weight: int | None = None,
    glow: float = 0.0,
    glow_color=None,
    z: int = 12,
    effect_options: dict | None = None,
):
    """发射闪动文字特效。坐标使用全局屏幕坐标，按中心点定位。"""
    options = {
        "center_pos": center_pos,
        "text": str(text or ""),
        "fade_in_duration": fade_in_duration,
        "fade_in_frequency": fade_in_frequency,
        "hold_duration": hold_duration,
        "fade_out_duration": fade_out_duration,
        "fade_out_frequency": fade_out_frequency,
        "font_type": font_type,
        "font_size": int(font_size),
        "color": tuple(color),
        "font_bold": bool(bold),
        "glow": glow,
    }
    if glow_color is not None:
        options["glow_color"] = tuple(glow_color)
    if font_weight is not None:
        options["font_weight"] = int(font_weight)
    options.update(dict(effect_options or {}))
    options.setdefault("z", int(z))

    spawn_effect(
        effect_id="flash_text",
        anchor_type="point",
        anchor_data=center_pos,
        effect_options=options,
    )
