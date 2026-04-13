"""雪球破碎粒子 - snow 的轻量化版本

相比 snow（雪豹消失特效）：
  - 粒子数量减半（count_range 减半）
  - 寿命减半（life_decay 翻倍）
  - 无屏幕边界碰撞反弹，粒子直接飞出后消亡

用于雪球淡出时的破碎飞溅效果。
"""
import random
import math
from typing import Tuple

from PyQt5.QtGui import QColor

from lib.script.practical.base_particle import BaseParticleScript
from lib.core.plugin_registry import register_particle


@register_particle("snowball_burst")
class SnowballBurstParticleScript(BaseParticleScript):
    """雪球碎裂时向四周扩散的白色球形粒子（轻量化，无边界碰撞）"""

    PARTICLE_ID = "snowball_burst"

    def __init__(self):
        super().__init__()
        self._config = {
            'count_range': (3, 4),             # snow 的一半：(6,8) → (3,4)
            'radius_range': (2, 5),            # 与 snow 相同
            'speed_range':  (1.5, 4),          # 与 snow 相同
            'gravity':      0.2,               # 与 snow 相同
            'drag':         0.97,              # 与 snow 相同
            'life_decay':   0.06,              # snow 的两倍：0.03 → 0.06（寿命减半）
            'color':        QColor(255, 255, 255),
        }

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        """在指定位置生成雪球破碎粒子，向四周随机方向扩散"""
        if area_type == 'circle':
            cx, cy, _ = area_data
        elif area_type == 'rect':
            x1, y1, x2, y2 = area_data
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        else:
            cx, cy = area_data[0], area_data[1]

        count = random.randint(*self._config['count_range'])
        return [SnowballBurstParticle(cx, cy, self._config) for _ in range(count)]


class SnowballBurstParticle:
    """单个雪球破碎粒子（无边界碰撞，直接消亡）"""

    is_circle = True  # 渲染层使用 drawEllipse

    def __init__(self, x: float, y: float, config: dict):
        self.x = float(x)
        self.y = float(y)

        angle = random.uniform(0, math.pi * 2)
        speed = random.uniform(*config['speed_range'])
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed

        self.size    = random.randint(*config['radius_range'])
        self.color   = config['color']
        self.gravity = config['gravity']
        self.drag    = config['drag']

        self.life     = 1.0
        self.max_life = 1.0
        self.life_decay = config['life_decay']

    def update(self):
        """物理更新：阻力 → 重力 → 位移 → 生命衰减（无边界碰撞）"""
        self.vx *= self.drag
        self.vy *= self.drag
        self.vy += self.gravity
        self.x  += self.vx
        self.y  += self.vy
        self.life -= self.life_decay

    @property
    def alive(self) -> bool:
        return self.life > 0
