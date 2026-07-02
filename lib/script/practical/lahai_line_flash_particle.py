"""Explosive gravity particles for Lahai Tetris line clear."""

from __future__ import annotations

import math
import random
from typing import Tuple

from PyQt5.QtGui import QColor

from lib.core.plugin_registry import register_particle
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta


@register_particle("lahai_line_flash")
class LahaiLineFlashParticleScript(BaseParticleScript):
    PARTICLE_ID = "lahai_line_flash"

    def __init__(self) -> None:
        super().__init__()
        self._config = {
            "count_range": (2, 3),
            "size_range": (2, 4),
            "speed_x": (-204.0, 204.0),
            "speed_y": (-276.0, -120.0),
            "gravity": 10.8,
            "drag": 0.912673,
            "brownian": 4.2,
            "life_decay": 0.036,
        }
        self._white_mix_ratio = 0.25
        self._count_scale = 1.25
        self._request_options: dict = {}

    def set_request_options(self, options: dict) -> None:
        self._request_options = dict(options or {})

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        segment_particles = self._create_segment_particles()
        if segment_particles is not None:
            return segment_particles

        if area_type == "rect":
            x1, y1, x2, y2 = area_data
        else:
            cx, cy = area_data[:2]
            x1 = cx - 20
            x2 = cx + 20
            y1 = cy - 2
            y2 = cy + 2

        base_count = random.randint(*self._config["count_range"])
        total_count = max(1, int(math.ceil(base_count * self._count_scale)))
        white_count = min(total_count, max(1, int(math.ceil(base_count * self._white_mix_ratio))))
        colored_count = max(0, total_count - white_count)

        particles = [
            LahaiLineFlashParticle(
                random.uniform(x1, x2),
                random.uniform(y1, y2),
                self._config,
                dict(self._request_options),
            )
            for _ in range(colored_count)
        ]
        particles.extend(
            LahaiLineFlashParticle(
                random.uniform(x1, x2),
                random.uniform(y1, y2),
                self._config,
                dict(self._request_options),
                fixed_rgb=(255, 255, 255),
                fixed_size=2,
            )
            for _ in range(white_count)
        )
        return particles

    def _create_segment_particles(self) -> list | None:
        segments = self._request_options.get("segments")
        if not segments:
            return None
        particles: list[LahaiLineFlashParticle] = []
        for segment in segments:
            rect = segment.get("rect") if isinstance(segment, dict) else None
            rgb = segment.get("rgb") if isinstance(segment, dict) else None
            if not isinstance(rect, (tuple, list)) or len(rect) != 4:
                continue
            x1, y1, x2, y2 = rect
            options = dict(self._request_options)
            if rgb is not None:
                options["rgb"] = rgb

            base_count = random.randint(*self._config["count_range"])
            total_count = max(1, int(math.ceil(base_count * self._count_scale)))
            white_count = min(total_count, max(1, int(math.ceil(base_count * self._white_mix_ratio))))
            colored_count = max(0, total_count - white_count)
            particles.extend(
                LahaiLineFlashParticle(
                    random.uniform(x1, x2),
                    random.uniform(y1, y2),
                    self._config,
                    options,
                )
                for _ in range(colored_count)
            )
            particles.extend(
                LahaiLineFlashParticle(
                    random.uniform(x1, x2),
                    random.uniform(y1, y2),
                    self._config,
                    options,
                    fixed_rgb=(255, 255, 255),
                    fixed_size=2,
                )
                for _ in range(white_count)
            )
        return particles


class LahaiLineFlashParticle:
    def __init__(
        self,
        x: float,
        y: float,
        config: dict,
        options: dict,
        *,
        fixed_rgb: tuple[int, int, int] | None = None,
        fixed_size: int | None = None,
    ) -> None:
        self.x = float(x)
        self.y = float(y)
        self.size = int(fixed_size) if fixed_size is not None else random.randint(*config["size_range"])
        self.vx = per_second_delta(random.uniform(*config["speed_x"]))
        self.vy = per_second_delta(random.uniform(*config["speed_y"]))
        base_rgb = fixed_rgb if fixed_rgb is not None else options.get("rgb", (255, 255, 255))
        self.color = QColor(*base_rgb) if fixed_rgb is not None else _vary_color(base_rgb)
        self.gravity = per_second_delta(float(config["gravity"]))
        self.drag = float(config["drag"])
        self._brownian = per_second_delta(float(config["brownian"]))
        self.life_decay = float(config["life_decay"])
        self.max_life = 1.0
        self.life = 1.0

    def update(self) -> None:
        self.vx += random.uniform(-self._brownian, self._brownian)
        self.vy += random.uniform(-self._brownian, self._brownian)
        self.vx *= self.drag
        self.vy *= self.drag
        self.vy += self.gravity
        self.x += self.vx
        self.y += self.vy
        self.life -= self.life_decay

    @property
    def alive(self) -> bool:
        return self.life > 0.0


def _vary_color(rgb: tuple[int, int, int] | list[int]) -> QColor:
    r, g, b = [max(0, min(255, int(v))) for v in rgb]
    luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    jitter = 20 if luma < 0.65 else 14
    return QColor(
        max(0, min(255, r + random.randint(-jitter, jitter))),
        max(0, min(255, g + random.randint(-jitter, jitter))),
        max(0, min(255, b + random.randint(-jitter, jitter))),
    )
