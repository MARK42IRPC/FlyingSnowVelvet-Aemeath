"""Sci-fi vertical rise particle for Lahai preview replacement."""

from __future__ import annotations

import random
from typing import Tuple

from lib.core.graphics.types import Color
from lib.core.plugin_registry import register_particle
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta, tick_seconds


@register_particle("lahai_preview_rise")
class LahaiPreviewRiseParticleScript(BaseParticleScript):
    PARTICLE_ID = "lahai_preview_rise"

    def __init__(self) -> None:
        super().__init__()
        self._config = {
            "count_range": (2, 4),
            "width_range": (3, 5),
            "height_range": (10, 16),
            "base_speed_range": (86.4, 141.6),
            "speed_random_scale": 0.25,
            "life_range": (0.7, 1.2),
            "spawn_jitter_x": 6.0,
            "spawn_jitter_y": 4.0,
        }
        self._request_options: dict = {}

    def set_request_options(self, options: dict) -> None:
        self._request_options = dict(options or {})

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        if area_type == "rect":
            x1, y1, x2, y2 = area_data
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
        elif area_type == "circle":
            cx, cy, _ = area_data
        else:
            cx, cy = area_data[:2]
        options = dict(self._request_options)
        return [
            LahaiPreviewRiseParticle(cx, cy, self._config, options)
            for _ in range(random.randint(*self._config["count_range"]))
        ]


class LahaiPreviewRiseParticle:
    def __init__(self, x: float, y: float, config: dict, options: dict) -> None:
        jitter_x = float(config.get("spawn_jitter_x", 0.0))
        jitter_y = float(config.get("spawn_jitter_y", 0.0))
        self.x = float(x) + random.uniform(-jitter_x, jitter_x)
        self.y = float(y) + random.uniform(-jitter_y, jitter_y)
        self.width = random.randint(*config["width_range"])
        self.height = random.randint(*config["height_range"])
        self.vx = 0.0
        base_speed = random.uniform(*config["base_speed_range"])
        speed_random_scale = max(0.0, float(config.get("speed_random_scale", 0.0)))
        varied_speed = base_speed * random.uniform(1.0 - speed_random_scale, 1.0 + speed_random_scale)
        self.vy = -per_second_delta(varied_speed)
        self._base_color = _vary_color(options.get("rgb", (255, 134, 88)))
        self.color = self._base_color
        self.max_life = random.uniform(*config["life_range"])
        self.life = self.max_life
        self._flash_toggle = random.choice((True, False))

    def update(self) -> None:
        self.y += self.vy
        self._flash_toggle = not self._flash_toggle
        self.color = self._base_color.lighter(132 if self._flash_toggle else 92)
        self.life -= tick_seconds()

    @property
    def alive(self) -> bool:
        return self.life > 0.0


def _vary_color(rgb: tuple[int, int, int] | list[int]) -> Color:
    r, g, b = [max(0, min(255, int(v))) for v in rgb]
    return Color(
        max(0, min(255, r + random.randint(-10, 16))),
        max(0, min(255, g + random.randint(-14, 10))),
        max(0, min(255, b + random.randint(-10, 8))),
    )
