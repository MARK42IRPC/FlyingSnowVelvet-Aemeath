"""雪花粒子效果脚本 - 向四周扩散、受重力影响的白色球形粒子"""
import random
import math
from typing import Tuple

from PyQt5.QtGui import QColor

from lib.script.practical.base_particle import BaseParticleScript, per_second_delta
from lib.core.plugin_registry import register_particle


@register_particle("snow")
class SnowParticleScript(BaseParticleScript):
    """雪豹消失时向四周扩散的白色雪花球形粒子"""

    PARTICLE_ID = "snow"

    def __init__(self):
        super().__init__()
        self._config = {
            'count_range': (6, 8),
            'radius_range': (2, 5),    # 粒子半径（像素）
            'speed_range':  (90, 240),  # 初始速度 px/s
            'gravity':      12.0,       # 重力加速度（每 tick 累加到 vy）
            'drag':         0.912673,   # 每 tick 空气阻力系数（约等于旧 0.97^3）
            'life_decay':   0.09,       # 每 tick 生命衰减（约等于旧 0.03*3）
            'color':        QColor(255, 255, 255),  # 纯白色
        }

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        """在指定位置生成雪花粒子，向四周随机方向扩散"""
        # 统一取中心点（snow 粒子只用 point 区域）
        if area_type == 'circle':
            cx, cy, _ = area_data
        elif area_type == 'rect':
            x1, y1, x2, y2 = area_data
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        else:
            cx, cy = area_data[0], area_data[1]

        count = random.randint(*self._config['count_range'])
        return [SnowParticle(cx, cy, self._config) for _ in range(count)]


class SnowParticle:
    """单个雪花球形粒子"""

    # 标记为球形，qt_particle_system 的 paintEvent 据此使用 drawEllipse
    is_circle = True

    def __init__(self, x: float, y: float, config: dict):
        self.x = float(x)
        self.y = float(y)

        # 向四周随机方向发射（全角度）
        angle = random.uniform(0, math.pi * 2)
        speed = random.uniform(*config['speed_range'])
        self.vx = per_second_delta(math.cos(angle) * speed)
        self.vy = per_second_delta(math.sin(angle) * speed)

        # size 在圆形粒子中表示半径
        self.size    = random.randint(*config['radius_range'])
        self.color   = config['color']
        self.gravity = per_second_delta(config['gravity'])
        self.drag    = float(config['drag'])

        self.life     = 1.0
        self.max_life = 1.0
        self.life_decay = float(config['life_decay'])

    def update(self):
        """物理更新：阻力 → 重力 → 位移 → 生命衰减"""
        self.vx *= self.drag
        self.vy *= self.drag
        self.vy += self.gravity   # 重力向下
        self.x  += self.vx
        self.y  += self.vy
        self.life -= self.life_decay

    @property
    def alive(self) -> bool:
        return self.life > 0
