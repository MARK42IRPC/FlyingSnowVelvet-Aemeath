"""Backend-neutral state for flashing text effects."""

from __future__ import annotations

import math
from typing import Any, Dict

from lib.script.plugin_registry import register_effect
from lib.script.effects.base_effect import BaseEffectScript, clamp01, tick_seconds


def _to_duration(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _to_frequency(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = int(default)
    if minimum is not None:
        result = max(int(minimum), result)
    if maximum is not None:
        result = min(int(maximum), result)
    return result


def _to_optional_weight(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, min(99, int(value)))
    except (TypeError, ValueError):
        return None


def _to_local_point(value, offset_x: float, offset_y: float) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError(f"invalid point: {value!r}")
    return float(value[0]) - offset_x, float(value[1]) - offset_y


def _to_color(value: Any, default: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, int]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return (
                max(0, min(255, int(value[0]))),
                max(0, min(255, int(value[1]))),
                max(0, min(255, int(value[2]))),
            )
        except (TypeError, ValueError):
            pass
    return default


def _flash_gate(elapsed: float, frequency: float, *, floor: float = 0.20) -> float:
    if frequency <= 0.0:
        return 1.0
    phase = max(0.0, float(elapsed)) * frequency * math.tau
    wave = 0.5 + 0.5 * math.sin(phase - math.pi / 2.0)
    return floor + (1.0 - floor) * wave


class FlashTextEffect:
    """Single flashing text effect instance."""

    def __init__(
        self,
        text: str,
        center_pos: tuple[float, float],
        fade_in_duration: float,
        fade_in_frequency: float,
        hold_duration: float,
        fade_out_duration: float,
        fade_out_frequency: float,
        *,
        font_type: str = "ui",
        font_size: int = 32,
        font_bold: bool = False,
        font_weight: int | None = None,
        color: tuple[int, int, int] = (255, 255, 255),
        glow: float = 0.0,
        glow_color: tuple[int, int, int] = (255, 255, 255),
        z: int = 10,
    ) -> None:
        self.text = str(text)
        self.center_pos = (float(center_pos[0]), float(center_pos[1]))
        self.fade_in_duration = max(0.0, float(fade_in_duration))
        self.fade_in_frequency = max(0.0, float(fade_in_frequency))
        self.hold_duration = max(0.0, float(hold_duration))
        self.fade_out_duration = max(0.0, float(fade_out_duration))
        self.fade_out_frequency = max(0.0, float(fade_out_frequency))
        self.font_type = str(font_type or "ui")
        self.font_size = max(1, int(font_size))
        self.font_bold = bool(font_bold)
        self.font_weight = _to_optional_weight(font_weight)
        self.color = tuple(color)
        self.glow = max(0.0, float(glow))
        self.glow_color = tuple(glow_color)
        self.total_duration = self.fade_in_duration + self.hold_duration + self.fade_out_duration
        self.max_life = self.total_duration
        self.life = self.total_duration
        self.age = 0.0
        self.x = self.center_pos[0]
        self.y = self.center_pos[1]
        self.opacity = 0.0
        self.scale = 1.0
        self.rotation = 0.0
        self.z = int(z)
        self._apply_state(0.0)

    def _sample_opacity(self, age: float) -> float:
        fade_in_end = self.fade_in_duration
        hold_end = fade_in_end + self.hold_duration
        fade_out_end = hold_end + self.fade_out_duration

        if self.fade_in_duration > 0.0 and age < fade_in_end:
            envelope = clamp01(age / self.fade_in_duration)
            return clamp01(envelope * _flash_gate(age, self.fade_in_frequency))

        if age < hold_end:
            return 1.0

        if self.fade_out_duration > 0.0 and age < fade_out_end:
            elapsed = age - hold_end
            envelope = 1.0 - clamp01(elapsed / self.fade_out_duration)
            return clamp01(envelope * _flash_gate(elapsed, self.fade_out_frequency))

        return 0.0

    def _apply_state(self, age: float) -> None:
        self.x = self.center_pos[0]
        self.y = self.center_pos[1]
        self.opacity = self._sample_opacity(age)

    def update(self) -> None:
        self.age = min(self.total_duration, self.age + tick_seconds())
        self.life = max(0.0, self.total_duration - self.age)
        self._apply_state(self.age)

    def apply_frame_interpolation(self, alpha: float) -> None:
        prev_age = float(getattr(self, "_tick_prev_age", self.age))
        cur_age = float(self.age)
        render_age = prev_age + (cur_age - prev_age) * clamp01(alpha)
        self._render_x = float(self.center_pos[0])
        self._render_y = float(self.center_pos[1])
        self._render_opacity = float(self._sample_opacity(render_age))
        self._render_scale = 1.0
        self._render_rotation = 0.0

    @property
    def alive(self) -> bool:
        return self.age < self.total_duration and self.opacity > 0.0


@register_effect("flash_text")
class FlashTextEffectScript(BaseEffectScript):
    """Render flashing text on the effect overlay."""

    EFFECT_ID = "flash_text"

    def create_effects(
        self,
        anchor_type: str,
        anchor_data,
        effect_options: Dict[str, Any] | None = None,
        request_context: Dict[str, Any] | None = None,
    ) -> list:
        options = dict(effect_options or {})
        context = dict(request_context or {})
        text = str(options.get("text", "") or "").strip()
        if not text:
            return []

        offset_x = float(context.get("offset_x", 0.0))
        offset_y = float(context.get("offset_y", 0.0))
        center_source = options.get("center_pos", anchor_data)
        try:
            center_pos = _to_local_point(center_source, offset_x, offset_y)
        except ValueError:
            return []

        fade_in_duration = _to_duration(options.get("fade_in_duration"))
        fade_in_frequency = _to_frequency(options.get("fade_in_frequency"))
        hold_duration = _to_duration(options.get("hold_duration"))
        fade_out_duration = _to_duration(options.get("fade_out_duration"))
        fade_out_frequency = _to_frequency(options.get("fade_out_frequency"))
        if (fade_in_duration + hold_duration + fade_out_duration) <= 0.0:
            return []

        color = _to_color(options.get("color", options.get("rgb", (255, 255, 255))))
        glow = max(0.0, float(options.get("glow", options.get("bloom", 0.0)) or 0.0))
        glow_color = _to_color(options.get("glow_color", options.get("glow_rgb", color)))

        return [
            FlashTextEffect(
                text=text,
                center_pos=center_pos,
                fade_in_duration=fade_in_duration,
                fade_in_frequency=fade_in_frequency,
                hold_duration=hold_duration,
                fade_out_duration=fade_out_duration,
                fade_out_frequency=fade_out_frequency,
                font_type=str(options.get("font_type", "ui")),
                font_size=_to_int(options.get("font_size", options.get("size", 32)), 32, minimum=1),
                font_bold=bool(options.get("font_bold", options.get("bold", False))),
                font_weight=_to_optional_weight(options.get("font_weight")),
                color=color,
                glow=glow,
                glow_color=glow_color,
                z=_to_int(options.get("z", 12), 12),
            )
        ]
