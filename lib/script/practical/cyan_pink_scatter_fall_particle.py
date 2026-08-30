"""青粉混合向四周扩散并下落粒子效果脚本"""
import random
import math
from typing import Tuple

from config.config import COLORS
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta
from lib.script.plugin_registry import register_particle


@register_particle("cyan_pink_scatter_fall")
class CyanPinkScatterFallParticleScript(BaseParticleScript):
    """青粉混合向四周扩散并下落粒子效果（左键点击使用）"""

    PARTICLE_ID = "cyan_pink_scatter_fall"

    def __init__(self):
        super().__init__()
        self._config = {
            'count_range': (10, 15),
            'size_range': (4, 8),
            'speed_x_range': (270, 540),      # px/s，初段横向喷散（提升 3 倍）
            'speed_y_range': (-360, -165),    # px/s，初段轻微上抛（提升 3 倍）
            'drift_x_range': (18, 42),        # px/s，后续缓慢横漂
            'fall_speed_range': (35, 72),     # px/s，后续稳定下落
            'brownian_range': (10, 26),       # px/s，横向布朗扰动
            'life_range': (0.95, 1.35),       # 秒
            'colors': [COLORS['cyan'], COLORS['pink']],  # 青色和粉色
        }

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        """创建青粉混合粒子"""
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

            # 随机选择颜色（青色或粉色）
            particle = CyanPinkScatterFallParticle(x, y, self._config)
            particles.append(particle)

        return particles


class CyanPinkScatterFallParticle:
    """单个青粉混合向四周扩散并下落粒子"""

    def __init__(self, x: float, y: float, config: dict):
        self.x = float(x)
        self.y = float(y)

        # 初段小幅喷散，随后进入慢漂下落。
        self.vx = per_second_delta(
            random.choice((-1.0, 1.0)) * random.uniform(*config['speed_x_range'])
        )
        self.vy = per_second_delta(random.uniform(*config['speed_y_range']))
        self._target_drift_x = per_second_delta(
            random.choice((-1.0, 1.0)) * random.uniform(*config['drift_x_range'])
        )
        self._fall_speed = per_second_delta(random.uniform(*config['fall_speed_range']))
        self._brownian = per_second_delta(random.uniform(*config['brownian_range']))
        self._phase = random.uniform(0, math.tau)
        self._wobble_speed = random.uniform(0.10, 0.24)

        # 外观 - 随机选择青色或粉色
        self.size = random.randint(*config['size_range'])
        self.color = random.choice(config['colors'])

        # 生命值 0~1
        self.max_life = random.uniform(*config['life_range'])
        self.life = self.max_life

    def update(self):
        """更新位置和生命值"""
        life_ratio = max(0.0, self.life / self.max_life)
        self.vx = self.vx * 0.88 + self._target_drift_x * 0.12
        self.vy = self.vy * 0.80 + self._fall_speed * 0.20
        self._phase += self._wobble_speed
        wobble_x = math.sin(self._phase) * self._brownian * (0.60 + 0.40 * life_ratio)
        jitter_x = random.uniform(-self._brownian, self._brownian) * 0.40
        jitter_y = random.uniform(-self._brownian, self._brownian) * 0.10

        self.x += self.vx + wobble_x + jitter_x
        self.y += self.vy + jitter_y

        # TICK 固定 20Hz，这里直接按秒衰减，避免受 frame_fps 配置影响。
        self.life -= 1.0 / 20.0

    @property
    def alive(self) -> bool:
        return self.life > 0
