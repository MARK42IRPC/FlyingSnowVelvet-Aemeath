"""Directional drift particles for Lahai Tetris."""

from __future__ import annotations

import random
from typing import Tuple

from PyQt5.QtGui import QColor

from lib.core.plugin_registry import register_particle
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta

@register_particle("lahai_glow_burst")
class LahaiGlowBurstParticleScript(BaseParticleScript):
    PARTICLE_ID = "lahai_glow_burst"

    def __init__(self) -> None:
        super().__init__()
        self._config = {
            "count_range": (4, 6),
            "size_range": (2, 4),
            "speed_range": (168.0, 288.0),
            "brownian": 4.8,
            "drag": 0.898632,
            "life_decay": 0.165,
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
            cx, cy = area_data
        options = dict(self._request_options)
        return [
            LahaiGlowBurstParticle(cx, cy, self._config, options)
            for _ in range(random.randint(*self._config["count_range"]))
        ]


class LahaiGlowBurstParticle:
    def __init__(self, x: float, y: float, config: dict, options: dict) -> None:
        self.x = float(x)
        self.y = float(y)
        dir_x, dir_y = options.get("direction", (0.0, -1.0))
        base_len = max(0.001, (dir_x * dir_x + dir_y * dir_y) ** 0.5)
        dir_x /= base_len
        dir_y /= base_len
        speed = per_second_delta(random.uniform(*config["speed_range"]))
        self.vx = dir_x * speed + per_second_delta(random.uniform(-15.0, 15.0))
        self.vy = dir_y * speed + per_second_delta(random.uniform(-15.0, 15.0))
        self.size = random.randint(*config["size_range"])
        self.color = _vary_color(options.get("rgb", (117, 233, 255)))
        self._brownian = per_second_delta(float(config["brownian"]))
        self.drag = float(config["drag"])
        self.life_decay = float(config["life_decay"])
        self.life = 1.0
        self.max_life = 1.0

    def update(self) -> None:
        self.vx += random.uniform(-self._brownian, self._brownian)
        self.vy += random.uniform(-self._brownian, self._brownian)
        self.vx *= self.drag
        self.vy *= self.drag
        self.x += self.vx
        self.y += self.vy
        self.life -= self.life_decay

    @property
    def alive(self) -> bool:
        return self.life > 0.0


def _vary_color(rgb: tuple[int, int, int] | list[int]) -> QColor:
    r, g, b = [max(0, min(255, int(v))) for v in rgb]
    luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    jitter = 18 if luma < 0.65 else 12
    return QColor(
        max(0, min(255, r + random.randint(-jitter, jitter))),
        max(0, min(255, g + random.randint(-jitter, jitter))),
        max(0, min(255, b + random.randint(-jitter, jitter))),
    )
