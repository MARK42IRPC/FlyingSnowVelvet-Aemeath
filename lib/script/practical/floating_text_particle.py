"""Floating text particle with fade in/out and upward easing."""

from __future__ import annotations

import math
import random
from typing import Tuple

from config.font_config import get_digit_font_family, get_ui_font_family
from lib.core.graphics.types import Color, FontSpec
from lib.core.plugin_registry import register_particle
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta, tick_seconds


_FADE_IN_SECS = 0.1
_MOVE_SECS = 0.2
_HOLD_SECS = 0.3
_FADE_OUT_SECS = 0.2
_TOTAL_SECS = _MOVE_SECS + _HOLD_SECS + _FADE_OUT_SECS


@register_particle("floating_text")
class FloatingTextParticleScript(BaseParticleScript):
    PARTICLE_ID = "floating_text"

    def __init__(self) -> None:
        super().__init__()
        self._request_options: dict = {}

    def set_request_options(self, options: dict) -> None:
        self._request_options = dict(options or {})

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        if area_type == "rect":
            x1, y1, x2, y2 = area_data
            x = (x1 + x2) / 2.0
            y = (y1 + y2) / 2.0
        else:
            x, y = area_data[:2]
        options = dict(self._request_options)
        return [FloatingTextParticle(x, y, options)]


class FloatingTextParticle:
    def __init__(self, x: float, y: float, options: dict) -> None:
        self.x = float(x)
        self.y = float(y)
        self._start_x = float(x)
        self._start_y = float(y)
        self._target_x = float(options.get("target_x", x))
        self._target_y = float(options.get("target_y", y))
        self.is_text = True
        self.text = str(options.get("text", ""))
        rgb = options.get("rgb", (255, 255, 255))
        self.color = Color(*(max(0, min(255, int(v))) for v in rgb))
        self.font = _build_font(
            str(options.get("font_type", "digit")),
            int(options.get("size", 18)),
            bold=bool(options.get("font_bold", options.get("bold", False))),
        )
        self.max_life = _TOTAL_SECS
        self.life = self.max_life
        self.alpha_override = 0
        self.bloom = max(0.0, float(options.get("bloom", 0.0)))
        self._drift_amplitude = max(0.0, float(options.get("drift_amplitude", 0.0)))
        self._drift_speed = max(0.0, float(options.get("drift_speed", 0.0)))
        self._drift_phase = random.uniform(0.0, math.tau)

    def update(self) -> None:
        elapsed = self.max_life - self.life
        if elapsed < _MOVE_SECS:
            progress = max(0.0, min(1.0, elapsed / _MOVE_SECS))
            eased = 1.0 - (1.0 - progress) * (1.0 - progress)
            self.x = self._start_x + (self._target_x - self._start_x) * eased
            self.y = self._start_y + (self._target_y - self._start_y) * eased
            fade_in_progress = max(0.0, min(1.0, elapsed / _FADE_IN_SECS))
            self.alpha_override = int(255 * fade_in_progress)
        elif elapsed < (_MOVE_SECS + _HOLD_SECS):
            self.x = self._target_x
            self.y = self._target_y
            self.alpha_override = 255
        else:
            fade_elapsed = elapsed - _MOVE_SECS - _HOLD_SECS
            fade_progress = max(0.0, min(1.0, fade_elapsed / _FADE_OUT_SECS))
            self.x = self._target_x
            self.y = self._target_y
            self.alpha_override = int(255 * (1.0 - fade_progress))
        if self._drift_amplitude > 0.0 and self._drift_speed > 0.0:
            drift = math.sin(elapsed * self._drift_speed + self._drift_phase) * self._drift_amplitude
            self.x += drift
        self.life -= tick_seconds()

    @property
    def alive(self) -> bool:
        return self.life > 0.0


def _build_font(font_type: str, size: int, *, bold: bool = False) -> FontSpec:
    pixel_size = max(1, int(size))
    if font_type.lower() in {"digit", "number", "lahai"}:
        family = get_digit_font_family()
    else:
        family = get_ui_font_family()
    return FontSpec(family, pixel_size, bold=bold)
