"""丝滑图片展示特效。

阶段语义：
1. 从起始位置减速移动到展示位置，同时从透明淡入到不透明
2. 在展示位置保持一段时间
3. 从展示位置加速移动到淡出终点，同时淡出到透明
"""

from __future__ import annotations

from typing import Any, Dict

from PyQt5.QtGui import QPixmap

from lib.core.plugin_registry import register_effect
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


class SmoothImageShowEffect:
    """单个图片展示特效实例。"""

    def __init__(
        self,
        pixmap: QPixmap,
        intro_start_pos: tuple[float, float],
        intro_duration: float,
        display_pos: tuple[float, float],
        display_duration: float,
        outro_end_pos: tuple[float, float],
        outro_duration: float,
        scale: float,
        z: int = 0,
    ):
        self.pixmap = pixmap
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
        self.rotation = 0.0
        self.z = int(z)

        self.x = 0.0
        self.y = 0.0
        self.opacity = 0.0
        self._apply_state(0.0)

    def _sample_state(self, age: float) -> tuple[float, float, float]:
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

    def _apply_state(self, age: float) -> None:
        self.x, self.y, self.opacity = self._sample_state(age)

    def update(self) -> None:
        self.age = min(self.total_duration, self.age + tick_seconds())
        self.life = max(0.0, self.total_duration - self.age)
        self._apply_state(self.age)

    def apply_frame_interpolation(self, alpha: float) -> None:
        prev_age = float(getattr(self, "_tick_prev_age", self.age))
        cur_age = float(self.age)
        render_age = prev_age + (cur_age - prev_age) * max(0.0, min(1.0, float(alpha)))
        render_x, render_y, render_opacity = self._sample_state(render_age)
        self._render_x = float(render_x)
        self._render_y = float(render_y)
        self._render_opacity = float(render_opacity)
        self._render_scale = float(self.scale)
        self._render_rotation = float(self.rotation)

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
        pixmap = options.get("pixmap")
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
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
                pixmap=pixmap,
                intro_start_pos=intro_start_pos,
                intro_duration=intro_duration,
                display_pos=display_pos,
                display_duration=display_duration,
                outro_end_pos=outro_end_pos,
                outro_duration=outro_duration,
                scale=scale,
                z=int(options.get("z", 0)),
            )
        ]
