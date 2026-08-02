"""Flicker data particle effect.

Spec:
- point-based spawn
- spawn 1-3 particles
- horizontal rectangle, height 2-4 px, width:height = 4:1
- color changes every tick among white/light-pink/light-cyan/deep-pink/deep-blue
- slight Brownian motion
- no gravity
- life about 0.5-1.5 seconds
- fades out via global overlay alpha logic
"""

from __future__ import annotations

import random
from typing import Tuple

from lib.core.graphics.types import Color
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta, tick_seconds
from lib.core.plugin_registry import register_particle


_COLOR_WHITE = Color(255, 255, 255)
_COLOR_LIGHT_PINK = Color(255, 182, 193)
_COLOR_LIGHT_CYAN = Color(173, 216, 230)
_COLOR_DEEP_PINK = Color(255, 149, 164)
_COLOR_DEEP_BLUE = Color(58, 92, 176)


@register_particle("flicker_data")
class FlickerDataParticleScript(BaseParticleScript):
    """Flicker data particles with per-frame palette switching."""

    PARTICLE_ID = "flicker_data"

    def __init__(self) -> None:
        super().__init__()
        self._config = {
            "count_range": (1, 3),
            "height_range": (2, 4),
            "aspect_ratio": 4,
            "life_range": (0.5, 1.5),  # seconds
            "brownian": 3.6,           # px/s
            "max_speed": 24.0,         # px/s
            "spawn_offset_range": (-30, 30),  # px
            "colors": [
                _COLOR_WHITE,
                _COLOR_LIGHT_PINK,
                _COLOR_LIGHT_CYAN,
                _COLOR_DEEP_PINK,
                _COLOR_DEEP_BLUE,
            ],
        }
        self._request_options: dict = {}

    def set_request_options(self, options: dict) -> None:
        self._request_options = dict(options or {})

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        if area_type == "rect":
            x1, y1, x2, y2 = area_data
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
        elif area_type == "circle":
            cx, cy, _ = area_data
        else:  # point
            cx, cy = area_data

        count = random.randint(*self._config["count_range"])
        offset_min, offset_max = self._config["spawn_offset_range"]
        palette_override = self._request_options.get("rgb")
        config = dict(self._config)
        if palette_override is not None:
            custom = Color(*(max(0, min(255, int(v))) for v in palette_override))
            config["colors"] = [
                custom.lighter(150),
                custom.lighter(125),
                custom,
                custom.darker(110),
                custom.darker(140),
            ]
        particles = []
        for _ in range(count):
            spawn_x = cx + random.randint(offset_min, offset_max)
            spawn_y = cy + random.randint(offset_min, offset_max)
            particles.append(FlickerDataParticle(spawn_x, spawn_y, config))
        return particles


class FlickerDataParticle:
    """Single flickering rectangle particle."""

    def __init__(self, x: float, y: float, config: dict) -> None:
        self.x = float(x)
        self.y = float(y)

        self.height = random.randint(*config["height_range"])
        self.width = self.height * int(config["aspect_ratio"])

        self.vx = per_second_delta(random.uniform(-4.8, 4.8))
        self.vy = per_second_delta(random.uniform(-4.8, 4.8))
        self._brownian = per_second_delta(float(config["brownian"]))
        self._max_speed = per_second_delta(float(config["max_speed"]))

        self._colors = config["colors"]
        self.color = random.choice(self._colors)

        self.max_life = float(random.uniform(*config["life_range"]))
        self.life = self.max_life

    def update(self) -> None:
        self.vx += random.uniform(-self._brownian, self._brownian)
        self.vy += random.uniform(-self._brownian, self._brownian)

        self.vx = max(-self._max_speed, min(self._max_speed, self.vx))
        self.vy = max(-self._max_speed, min(self._max_speed, self.vy))

        self.x += self.vx
        self.y += self.vy

        self.color = random.choice(self._colors)
        self.life -= tick_seconds()

    @property
    def alive(self) -> bool:
        return self.life > 0.0
