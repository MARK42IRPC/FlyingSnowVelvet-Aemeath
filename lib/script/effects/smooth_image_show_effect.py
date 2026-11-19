"""丝滑图片展示特效。

阶段语义：
1. 从起始位置减速移动到展示位置，同时从透明淡入到不透明
2. 在展示位置保持一段时间
3. 从展示位置加速移动到淡出终点，同时淡出到透明
"""

from __future__ import annotations

import math
from typing import Any, Dict

from lib.script.plugin_registry import register_effect
from lib.script.effects.base_effect import (
    BaseEffectScript,
    clamp01,
    ease_in_cubic,
    ease_out_cubic,
    tick_seconds,
)


def _to_local_point(value, offset_x: float, offset_y: float) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError(f"无效坐标: {value!r}")
    return float(value[0]) - offset_x, float(value[1]) - offset_y


def _to_duration(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _to_scale(value: Any) -> float:
    try:
        return max(0.001, float(value))
    except (TypeError, ValueError):
        return 1.0


def _lerp_point(start: tuple[float, float], end: tuple[float, float], t: float) -> tuple[float, float]:
    return (
        start[0] + (end[0] - start[0]) * t,
        start[1] + (end[1] - start[1]) * t,
    )


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _orbit_vector(value) -> tuple[float, float]:
    if not isinstance(value, (tuple, list)) or len(value) < 2:
        return 0.0, 0.0
    return _finite_float(value[0]), _finite_float(value[1])


def _angular_duration(value) -> float | None:
    if value is None:
        return None
    duration = _finite_float(value, -1.0)
    return max(0.0, duration) if duration >= 0.0 else None


class SmoothImageShowEffect:
    """单个图片展示特效实例。"""

    def __init__(
        self,
        resource_path: str,
        intro_start_pos: tuple[float, float],
        intro_duration: float,
        display_pos: tuple[float, float],
        display_duration: float,
        outro_end_pos: tuple[float, float],
        outro_duration: float,
        scale: float,
        z: int = 0,
        *,
        animate: bool = False,
        playback_rate: float = 1.0,
        animation_loop: bool = True,
        hover_amplitude: float = 0.0,
        pulse_scale: float = 0.0,
        intro_scale: bool = False,
        rotation: float = 0.0,
        angular_velocity: float = 0.0,
        orbit_offset: tuple[float, float] = (0.0, 0.0),
        orbit_start_angle: float = 0.0,
        angular_duration: float | None = None,
    ):
        self.resource_path = str(resource_path)
        self.intro_start_pos = intro_start_pos
        self.intro_duration = max(0.0, float(intro_duration))
        self.display_pos = display_pos
        self.display_duration = max(0.0, float(display_duration))
        self.outro_end_pos = outro_end_pos
        self.outro_duration = max(0.0, float(outro_duration))

        self.age = 0.0
        self.total_duration = self.intro_duration + self.display_duration + self.outro_duration
        self.max_life = self.total_duration
        self.life = self.total_duration
        self.scale = max(0.001, float(scale))
        self.rotation = _finite_float(rotation)
        self.angular_velocity = _finite_float(angular_velocity)
        self.orbit_offset = _orbit_vector(orbit_offset)
        self.orbit_start_angle = _finite_float(orbit_start_angle)
        self.angular_duration = _angular_duration(angular_duration)
        self.animate = bool(animate)
        self.playback_rate = max(0.0, float(playback_rate))
        self.animation_loop = bool(animation_loop)
        self.hover_amplitude = max(0.0, float(hover_amplitude))
        self.pulse_scale = max(0.0, min(.5, float(pulse_scale)))
        self.intro_scale = bool(intro_scale)
        self._render_age = 0.0
        self.z = int(z)

        self.x = 0.0
        self.y = 0.0
        self.opacity = 0.0
        self._apply_state(0.0)

    def _sample_base_state(self, age: float) -> tuple[float, float, float]:
        intro_end = self.intro_duration
        display_end = intro_end + self.display_duration
        total_end = display_end + self.outro_duration

        if self.intro_duration > 0.0 and age < intro_end:
            t = ease_out_cubic(age / self.intro_duration)
            x, y = _lerp_point(self.intro_start_pos, self.display_pos, t)
            return x, y, t

        if age < display_end or self.outro_duration <= 0.0:
            x, y = self.display_pos
            opacity = 1.0 if age < total_end else 0.0
            return x, y, opacity

        if age < total_end:
            t = ease_in_cubic((age - display_end) / self.outro_duration)
            x, y = _lerp_point(self.display_pos, self.outro_end_pos, t)
            return x, y, 1.0 - clamp01(t)

        x, y = self.outro_end_pos
        return x, y, 0.0

    def _sample_state(self, age: float) -> tuple[float, float, float]:
        x, y, opacity = self._sample_base_state(age)
        ox, oy = self.orbit_offset
        if ox == 0.0 and oy == 0.0:
            return x, y, opacity
        angle = math.radians(self.orbit_start_angle + self.angular_velocity * self._angular_age(age))
        cosine, sine = math.cos(angle), math.sin(angle)
        return x + ox*cosine - oy*sine, y + ox*sine + oy*cosine, opacity

    def _sample_rotation(self, age: float) -> float:
        # Keep the full angular distance; modulo360 would reverse interpolation
        # at a wrap and make a fast full-circle sweep appear to stop.
        return self.rotation + self.angular_velocity * self._angular_age(age)

    def _angular_age(self, age: float) -> float:
        return age if self.angular_duration is None else min(age, self.angular_duration)

    def _apply_state(self, age: float) -> None:
        self.x, self.y, self.opacity = self._sample_state(age)
        self.y -= math.sin(age * 4.0) * self.hover_amplitude
        self._render_age = age
        self._render_scale = self._sample_scale(age)
        if self.angular_velocity != 0.0:
            self._render_rotation = self._sample_rotation(age)

    def _sample_scale(self, age: float) -> float:
        birth = ease_out_cubic(age / self.intro_duration) if self.intro_scale and self.intro_duration > 0 else 1.0
        return self.scale * max(.05, birth) * (1.0 + self.pulse_scale * math.sin(age * 11.0))

    def update(self) -> None:
        self.age = min(self.total_duration, self.age + tick_seconds())
        self.life = max(0.0, self.total_duration - self.age)
        self._apply_state(self.age)

    def apply_frame_interpolation(self, alpha: float) -> None:
        prev_age = float(getattr(self, "_tick_prev_age", self.age))
        cur_age = float(self.age)
        render_age = prev_age + (cur_age - prev_age) * max(0.0, min(1.0, float(alpha)))
        self._render_age = render_age
        render_x, render_y, render_opacity = self._sample_state(render_age)
        render_y -= math.sin(render_age * 4.0) * self.hover_amplitude
        self._render_x = float(render_x)
        self._render_y = float(render_y)
        self._render_opacity = float(render_opacity)
        self._render_scale = self._sample_scale(render_age)
        self._render_rotation = self._sample_rotation(render_age)

    @property
    def alive(self) -> bool:
        return self.age < self.total_duration and self.opacity > 0.0


@register_effect("smooth_image_show")
class SmoothImageShowEffectScript(BaseEffectScript):
    """图片展示特效：减速淡入，停留，随后加速淡出。"""

    EFFECT_ID = "smooth_image_show"

    def create_effects(
        self,
        anchor_type: str,
        anchor_data,
        effect_options: Dict[str, Any] | None = None,
        request_context: Dict[str, Any] | None = None,
    ) -> list:
        options = dict(effect_options or {})
        context = dict(request_context or {})
        resource_path = str(
            options.get("resolved_resource_path")
            or options.get("resource_path")
            or ""
        ).strip()
        if not resource_path:
            return []

        offset_x = float(context.get("offset_x", 0.0))
        offset_y = float(context.get("offset_y", 0.0))

        try:
            intro_start_pos = _to_local_point(options.get("intro_start_pos"), offset_x, offset_y)
            display_pos = _to_local_point(options.get("display_pos"), offset_x, offset_y)
            outro_end_pos = _to_local_point(options.get("outro_end_pos"), offset_x, offset_y)
        except ValueError:
            return []

        intro_duration = _to_duration(options.get("intro_duration"))
        display_duration = _to_duration(options.get("display_duration"))
        outro_duration = _to_duration(options.get("outro_duration"))
        scale = _to_scale(options.get("scale", 1.0))

        if (intro_duration + display_duration + outro_duration) <= 0.0:
            return []

        return [
            SmoothImageShowEffect(
                resource_path=resource_path,
                intro_start_pos=intro_start_pos,
                intro_duration=intro_duration,
                display_pos=display_pos,
                display_duration=display_duration,
                outro_end_pos=outro_end_pos,
                outro_duration=outro_duration,
                scale=scale,
                z=int(options.get("z", 0)),
                animate=bool(options.get("animate", False)),
                playback_rate=_to_duration(options.get("playback_rate", 1.0)),
                animation_loop=bool(options.get("animation_loop", True)),
                hover_amplitude=_to_duration(options.get("hover_amplitude", 0.0)),
                pulse_scale=_to_duration(options.get("pulse_scale", 0.0)),
                intro_scale=bool(options.get("intro_scale", False)),
                rotation=_finite_float(options.get("rotation", 0.0)),
                angular_velocity=_finite_float(options.get("angular_velocity", 0.0)),
                orbit_offset=_orbit_vector(options.get("orbit_offset", (0.0, 0.0))),
                orbit_start_angle=_finite_float(options.get("orbit_start_angle", 0.0)),
                angular_duration=options.get("angular_duration"),
            )
        ]
