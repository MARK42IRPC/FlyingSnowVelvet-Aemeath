"""向右移动并淡出粒子效果脚本"""
import random
import math
from typing import Tuple

from lib.core.graphics.types import Color
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta, tick_seconds
from lib.core.plugin_registry import register_particle


@register_particle("right_fade")
class RightFadeParticleScript(BaseParticleScript):
    """向右移动并淡出粒子效果 - 浅青色方形粒子"""

    PARTICLE_ID = "right_fade"

    def __init__(self):
        super().__init__()
        self._config = {
            'height_range': (1, 3),  # 高度 1~3px
            'width_range': (5, 15),  # 宽度 5~15px
            'base_speed': 60,  # 基础速度 60px/秒
            'speed_variation': 20,  # 速度随机变化 ±20px/秒
            'life_range': (0.3, 0.6),  # 寿命 0.3~0.6秒
            'color': Color(173, 216, 230),  # 浅青色
        }

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        """创建方形粒子，粒子数由面积决定：每1000像素面积生成1.5个粒子（向上取整）"""
        particles = []

        # 根据区域类型计算面积，进而确定粒子数量
        if area_type == 'rect':
            x1, y1, x2, y2 = area_data
            area = (x2 - x1) * (y2 - y1)
        elif area_type == 'circle':
            _, __, radius = area_data
            area = math.pi * radius ** 2
        else:
            area = 1  # 单点退化为最少1个粒子

        count = math.ceil(area * 1.5 / 1000)

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

            particle = RightFadeParticle(x, y, self._config)
            particles.append(particle)

        return particles


class RightFadeParticle:
    """单个向右移动并淡出粒子"""

    def __init__(self, x: float, y: float, config: dict):
        self.x = float(x)
        self.y = float(y)

        # 速度 - 向右移动，基础速度60px/秒，随机±20px/秒
        base_speed = config['base_speed']
        variation = config['speed_variation']
        speed = base_speed + random.uniform(-variation, variation)

        self.vx = per_second_delta(speed)
        self.vy = 0  # 仅水平移动

        # 外观 - 水平矩形
        self.height = random.randint(*config['height_range'])
        self.width = random.randint(*config['width_range'])
        self.color = config['color']

        # 寿命 - 随机0.3~0.6秒
        self.max_life = random.uniform(*config['life_range'])
        self.life = self.max_life

    def update(self):
        """更新位置和生命值"""
        self.x += self.vx
        self.y += self.vy
        self.life -= tick_seconds()

    @property
    def alive(self) -> bool:
        return self.life > 0
