"""向上移动并淡出粒子效果脚本"""
import random
import math
from typing import Tuple
from PyQt5.QtGui import QColor

from lib.script.practical.base_particle import BaseParticleScript, per_second_delta, tick_seconds
from lib.core.plugin_registry import register_particle


@register_particle("up_fade")
class UpFadeParticleScript(BaseParticleScript):
    """向上移动并淡出粒子效果 - 粉色正方形粒子"""

    PARTICLE_ID = "up_fade"

    def __init__(self):
        super().__init__()
        self._config = {
            'size_range': (2, 6),              # 边长 2~6px 正方形
            'base_speed': 60,                  # 基础速度 60px/秒（向上）
            'speed_variation': 20,             # 速度随机变化 ±20px/秒
            'life_range': (0.3, 0.6),          # 寿命 0.3~0.6秒
            'color': QColor(255, 182, 193),    # 淡粉色
        }

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        """创建正方形粒子，粒子数由面积决定：每500像素面积生成3个粒子（向上取整）"""
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

        count = math.ceil(area * 3 / 500)

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

            particle = UpFadeParticle(x, y, self._config)
            particles.append(particle)

        return particles


class UpFadeParticle:
    """单个向上移动并淡出粒子"""

    def __init__(self, x: float, y: float, config: dict):
        self.x = float(x)
        self.y = float(y)

        # 速度 - 纯向上移动（屏幕 Y 轴向下为正，向上为负）
        base_speed = config['base_speed']
        variation = config['speed_variation']
        speed = base_speed + random.uniform(-variation, variation)

        self.vx = 0               # 无水平偏移
        self.vy = -per_second_delta(speed)

        # 外观 - 正方形（使用 size 走渲染器的正方形分支）
        self.size = random.randint(*config['size_range'])
        # 30% 概率变异为黑色
        self.color = QColor(0, 0, 0) if random.random() < 0.3 else config['color']

        # 寿命 0.3~0.6秒，alpha = life * 255（与 right_fade 一致）
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
