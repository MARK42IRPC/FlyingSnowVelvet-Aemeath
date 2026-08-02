"""碰撞反弹粒子效果脚本"""
import random
import math
from typing import Tuple

from lib.core.graphics.types import Color
from lib.core.screen_utils import get_virtual_screen_rect
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta, tick_seconds
from lib.core.plugin_registry import register_particle


# 淡棕色调色板
_LIGHT_BROWNS = [
    Color(205, 175, 149),  # 浅棕褐
    Color(210, 180, 140),  # 棕褐（tan）
    Color(196, 164, 132),  # 中棕
    Color(188, 158, 120),  # 深棕
]

# 灰色调色板
_GRAYS = [
    Color(192, 192, 192),  # 银灰
    Color(176, 176, 176),  # 浅灰
    Color(160, 160, 160),  # 中灰
    Color(144, 144, 144),  # 深灰
]

_ALL_COLORS = _LIGHT_BROWNS + _GRAYS

# 物理常数（20Hz tick 语义）
_GRAVITY = per_second_delta(9.0)
_DRAG    = 0.912673


@register_particle("collision")
class CollisionParticleScript(BaseParticleScript):
    """碰撞粒子脚本 - 方形单点粒子，与屏幕边缘弹跳，淡出消退"""

    PARTICLE_ID = "collision"

    def __init__(self):
        super().__init__()
        self._config = {
            'count_range': (5, 8),       # 每次 5~8 个
            'size_range':  (2, 4),       # 边长 2~4px 正方形
            'speed_range': (75.0, 165.0), # 初速度 px/s
            'life_range':  (0.6, 1.0),   # 寿命 0.6~1.0 秒
            'colors':      _ALL_COLORS,  # 淡棕 + 灰色随机
        }

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        """在触发点生成碰撞粒子（始终取中心点）"""
        if area_type == 'circle':
            cx, cy, _ = area_data
        elif area_type == 'rect':
            x1, y1, x2, y2 = area_data
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        else:
            cx, cy = float(area_data[0]), float(area_data[1])

        screen = get_virtual_screen_rect()
        screen_w = float(screen.width)
        screen_h = float(screen.height)

        count = random.randint(*self._config['count_range'])
        return [
            CollisionParticle(cx, cy, screen_w, screen_h, self._config)
            for _ in range(count)
        ]


class CollisionParticle:
    """单个碰撞反弹方形粒子"""

    # 无 is_circle / is_line 标记 → 走渲染器正方形分支（size 为边长）

    def __init__(
        self,
        x: float,
        y: float,
        screen_w: float,
        screen_h: float,
        config: dict,
    ):
        self.x = x
        self.y = y

        # 随机全方向初速度
        angle = random.uniform(0.0, math.pi * 2.0)
        speed = per_second_delta(random.uniform(*config['speed_range']))
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed

        # 外观 - 正方形，size 为边长
        self.size = random.randint(*config['size_range'])
        self.color = random.choice(config['colors'])

        # 生命（秒），渲染层据此计算淡出 alpha
        self.max_life = random.uniform(*config['life_range'])
        self.life = self.max_life

        # 屏幕边界（用于碰撞反弹检测）
        self._screen_w = screen_w
        self._screen_h = screen_h

    def update(self) -> None:
        """物理更新：重力 → 空气阻力 → 移动 → 边缘碰撞反弹 → 生命衰减"""
        # 重力（仅作用于 vy）
        self.vy += _GRAVITY

        # 空气阻力（各方向等比衰减）
        self.vx *= _DRAG
        self.vy *= _DRAG

        self.x += self.vx
        self.y += self.vy

        half = self.size * 0.5

        # 水平边界
        if self.x - half < 0.0:
            self.x = half
            self.vx = abs(self.vx)
        elif self.x + half > self._screen_w:
            self.x = self._screen_w - half
            self.vx = -abs(self.vx)

        # 垂直边界
        if self.y - half < 0.0:
            self.y = half
            self.vy = abs(self.vy)
        elif self.y + half > self._screen_h:
            self.y = self._screen_h - half
            self.vy = -abs(self.vy)

        self.life -= tick_seconds()

    @property
    def alive(self) -> bool:
        return self.life > 0.0
