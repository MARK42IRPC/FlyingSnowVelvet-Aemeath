"""Shared six-armed frost flakes: outward scatter, gentle fall and spin."""
from __future__ import annotations

import math
import random

from lib.core.render.visuals.types import Color
from lib.script.plugin_registry import register_particle
from lib.script.practical.base_particle import BaseParticleScript, tick_seconds


def _range(value, fallback, minimum=0.0):
    try:
        low, high = value if isinstance(value, (tuple, list)) else (value, value)
        low, high = sorted((float(low), float(high)))
        if math.isfinite(low) and math.isfinite(high):
            return max(minimum, low), max(minimum, high)
    except (TypeError, ValueError):
        pass
    return fallback


class FrostSnowflake:
    """Pure particle state; ``size`` is diameter, matching ordinary bounds."""

    is_snowflake = True

    def __init__(self, x, y, rng, *, life, speed, size, color):
        angle = rng.uniform(0, math.tau)
        velocity = rng.uniform(*speed)
        self.x, self.y = float(x), float(y)
        self.vx, self.vy = math.cos(angle) * velocity, math.sin(angle) * velocity - 12
        self.size = rng.uniform(*size)
        self.color = color
        self.rotation = rng.uniform(0, 360)
        self.angular_velocity = rng.uniform(-130, 130)
        self.max_life = rng.uniform(*life)
        self.life = self.max_life

    def update(self):
        seconds = tick_seconds()
        self.vx *= math.exp(-3.2 * seconds)
        self.vy = self.vy * math.exp(-2.4 * seconds) + 32 * seconds
        self.x += self.vx * seconds
        self.y += self.vy * seconds
        self.rotation += self.angular_velocity * seconds
        self.life = max(0.0, self.life - seconds)

    @property
    def alive(self):
        return self.life > 1e-9


@register_particle('frost_snowflake')
class FrostSnowflakeParticleScript(BaseParticleScript):
    PARTICLE_ID = 'frost_snowflake'

    def __init__(self):
        super().__init__()
        self._request_options = {}

    def set_request_options(self, options):
        self._request_options = dict(options or {})

    def create_particles(self, area_type, area_data):
        options = self._request_options
        rng = random.Random(options.get('seed'))
        try:
            count = max(0, min(128, int(options.get('count', 14))))
        except (TypeError, ValueError, OverflowError):
            count = 14
        life = _range(options.get('life'), (.55, .9), .05)
        speed = _range(options.get('speed'), (35, 90))
        size = _range(options.get('size'), (8, 14), 1)
        rgb = options.get('rgb')
        palette = (Color(235, 253, 255), Color(137, 224, 255))
        if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
            try:
                palette = (Color(*(max(0, min(255, int(v))) for v in rgb[:3])),)
            except (TypeError, ValueError, OverflowError):
                pass
        particles = []
        for _ in range(count):
            if area_type == 'rect':
                x1, y1, x2, y2 = area_data
                x, y = rng.uniform(x1, x2), rng.uniform(y1, y2)
            elif area_type == 'circle':
                cx, cy, radius = area_data
                angle, distance = rng.uniform(0, math.tau), math.sqrt(rng.random()) * max(0, radius)
                x, y = cx + math.cos(angle) * distance, cy + math.sin(angle) * distance
            else:
                x, y = area_data[:2]
            particles.append(FrostSnowflake(x, y, rng, life=life, speed=speed, size=size,
                                            color=rng.choice(palette)))
        return particles
