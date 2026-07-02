"""Flashing text effect rendered as a glow text pixmap on the global overlay."""

from __future__ import annotations

import math
from typing import Any, Dict

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPixmap

from config.font_config import get_digit_font, get_ui_font
from lib.core.plugin_registry import register_effect
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


def _to_local_point(value, offset_x: float, offset_y: float) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError(f"invalid point: {value!r}")
    return float(value[0]) - offset_x, float(value[1]) - offset_y


def _to_color(value: Any, default: tuple[int, int, int] = (255, 255, 255)) -> QColor:
    if isinstance(value, QColor):
        return QColor(value)
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return QColor(
                max(0, min(255, int(value[0]))),
                max(0, min(255, int(value[1]))),
                max(0, min(255, int(value[2]))),
            )
        except (TypeError, ValueError):
            pass
    return QColor(*default)


def _build_font(font_type: str, size: int, *, bold: bool = False, weight: int | None = None) -> QFont:
    pixel_size = max(1, int(size))
    if str(font_type or "").lower() in {"digit", "number", "lahai"}:
        font = get_digit_font(pixel_size)
    else:
        font = get_ui_font(pixel_size)
    font.setBold(bool(bold))
    if weight is not None:
        try:
            font.setWeight(max(0, min(99, int(weight))))
        except (TypeError, ValueError):
            pass
    return font


def _render_text_pixmap(
    text: str,
    *,
    font: QFont,
    color: QColor,
    glow: float,
    glow_color: QColor | None = None,
) -> QPixmap:
    metrics = QFontMetrics(font)
    text_width = max(1, metrics.horizontalAdvance(text))
    text_height = max(1, metrics.height())
    glow_radius = max(0.0, float(glow))
    padding = int(math.ceil(glow_radius + max(4.0, text_height * 0.18)))
    image = QImage(
        text_width + padding * 2,
        text_height + padding * 2,
        QImage.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setFont(font)

    baseline_x = float(padding)
    baseline_y = float(padding + metrics.ascent())
    if glow_radius > 0.0:
        glow_tint = QColor(glow_color or color)
        glow_steps = (
            (1.0, 0.22),
            (0.72, 0.16),
            (0.45, 0.10),
        )
        for radius_scale, alpha_scale in glow_steps:
            radius = glow_radius * radius_scale
            glow_tint.setAlpha(max(0, min(255, int(255 * alpha_scale))))
            painter.setPen(glow_tint)
            for dx, dy in (
                (radius, 0.0),
                (-radius, 0.0),
                (0.0, radius),
                (0.0, -radius),
                (radius * 0.7, radius * 0.7),
                (-radius * 0.7, radius * 0.7),
                (radius * 0.7, -radius * 0.7),
                (-radius * 0.7, -radius * 0.7),
            ):
                painter.drawText(QPointF(baseline_x + dx, baseline_y + dy), text)

    painter.setPen(color)
    painter.drawText(QPointF(baseline_x, baseline_y), text)
    painter.end()
    return QPixmap.fromImage(image)


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
        pixmap: QPixmap,
        center_pos: tuple[float, float],
        fade_in_duration: float,
        fade_in_frequency: float,
        hold_duration: float,
        fade_out_duration: float,
        fade_out_frequency: float,
        *,
        z: int = 10,
    ) -> None:
        self.pixmap = pixmap
        self.center_pos = (float(center_pos[0]), float(center_pos[1]))
        self.fade_in_duration = max(0.0, float(fade_in_duration))
        self.fade_in_frequency = max(0.0, float(fade_in_frequency))
        self.hold_duration = max(0.0, float(hold_duration))
        self.fade_out_duration = max(0.0, float(fade_out_duration))
        self.fade_out_frequency = max(0.0, float(fade_out_frequency))
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

        font = _build_font(
            str(options.get("font_type", "ui")),
            int(options.get("font_size", options.get("size", 32))),
            bold=bool(options.get("font_bold", options.get("bold", False))),
            weight=options.get("font_weight"),
        )
        color = _to_color(options.get("color", options.get("rgb", (255, 255, 255))))
        glow = max(0.0, float(options.get("glow", options.get("bloom", 0.0)) or 0.0))
        glow_color = _to_color(options.get("glow_color", options.get("glow_rgb", color)))
        pixmap = _render_text_pixmap(
            text,
            font=font,
            color=color,
            glow=glow,
            glow_color=glow_color,
        )
        if pixmap.isNull():
            return []

        return [
            FlashTextEffect(
                pixmap=pixmap,
                center_pos=center_pos,
                fade_in_duration=fade_in_duration,
                fade_in_frequency=fade_in_frequency,
                hold_duration=hold_duration,
                fade_out_duration=fade_out_duration,
                fade_out_frequency=fade_out_frequency,
                z=int(options.get("z", 12)),
            )
        ]
