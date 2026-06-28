"""向四周扩散并下落粒子效果脚本"""
import random
import math
from typing import Tuple
from PyQt5.QtGui import QColor

from lib.script.practical.base_particle import BaseParticleScript, per_second_delta
from lib.core.plugin_registry import register_particle


@register_particle("scatter_fall")
class ScatterFallParticleScript(BaseParticleScript):
    """向四周扩散并下落粒子效果"""

    PARTICLE_ID = "scatter_fall"

    def __init__(self):
        super().__init__()
        self._config = {
            'count_range': (8, 12),
            'size_range': (4, 8),
            'speed_range': (120, 300),  # px/s
            'gravity': 10.5,            # px/s per tick
            'drag': 0.941192,           # per tick，约等于旧 0.98^3
            'life_decay': 0.15,         # per tick，约等于旧 0.05*3
            'color': QColor(173, 216, 230),  # 浅青色
        }

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        """创建雪花粒子"""
        particles = []
        count = random.randint(*self._config['count_range'])

        for _ in range(count):
            # 根据区域类型生成位置
            if area_type == 'rect':
                x1, y1, x2, y2 = area_data
                x = random.uniform(x1, x2)
                y = random.uniform(y1, y2)
            elif area_type == 'circle':
                cx, cy, radius = area_data
                angle = random.uniform(0, math.pi * 2)
                r = random.uniform(0, radius)
                x = cx + math.cos(angle) * r
                y = cy + math.sin(angle) * r
            else:
                x, y = area_data[0], area_data[1]

            particle = ScatterFallParticle(x, y, self._config)
            particles.append(particle)

        return particles


class ScatterFallParticle:
    """单个向四周扩散并下落粒子"""

    def __init__(self, x: float, y: float, config: dict):
        self.x = float(x)
        self.y = float(y)

        # 速度 - 向上飘散
        angle = random.uniform(0, math.pi * 2)
        speed = random.uniform(*config['speed_range'])
        self.vx = per_second_delta(math.cos(angle) * speed)
        self.vy = per_second_delta(math.sin(angle) * speed - random.uniform(120, 240))

        # 外观
        self.size = random.randint(*config['size_range'])
        self.color = config['color']

        # 生命值 0~1
        self.life = 1.0
        self.max_life = 1.0
        self.gravity = per_second_delta(config['gravity'])
        self.drag = float(config['drag'])
        self.life_decay = float(config['life_decay'])

    def update(self):
        """更新位置和生命值"""
        # 应用空气阻力（逐渐减速）
        self.vx *= self.drag
        self.vy *= self.drag

        # 应用重力
        self.vy += self.gravity

        # 更新位置
        self.x += self.vx
        self.y += self.vy

        # 减少生命值
        self.life -= self.life_decay

    @property
    def alive(self) -> bool:
        return self.life > 0
