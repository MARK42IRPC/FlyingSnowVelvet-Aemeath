"""白粉色碰撞反弹粒子效果脚本 - 音响专属"""
import random
import math
from typing import Tuple

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication

from lib.script.practical.base_particle import BaseParticleScript, per_second_delta, tick_seconds
from lib.core.plugin_registry import register_particle


# 白色调色板
_WHITES = [
    QColor(255, 255, 255),  # 纯白
    QColor(250, 250, 250),  # 烟白
    QColor(245, 245, 245),  # 浅灰白
    QColor(240, 240, 240),  # 淡灰白
]

# 粉色调色板
_PINKS = [
    QColor(255, 182, 193),  # 浅粉
    QColor(255, 192, 203),  # 粉红
    QColor(255, 174, 185),  # 淡玫瑰粉
    QColor(255, 160, 180),  # 柔粉
]

_ALL_COLORS = _WHITES + _PINKS

# 物理常数（20Hz tick 语义）
_GRAVITY = per_second_delta(9.0)
_DRAG    = 0.912673


@register_particle("white_pink_collision")
class WhitePinkCollisionParticleScript(BaseParticleScript):
    """白粉色碰撞粒子脚本 - 音响专属，方形单点粒子，与屏幕边缘弹跳，淡出消退"""

    PARTICLE_ID = "white_pink_collision"

    def __init__(self):
        super().__init__()
        self._config = {
            'count_range': (5, 8),       # 每次 5~8 个
            'size_range':  (2, 4),       # 边长 2~4px 正方形
            'speed_range': (75.0, 165.0), # 初速度 px/s
            'life_range':  (0.6, 1.0),   # 寿命 0.6~1.0 秒
            'colors':      _ALL_COLORS,  # 白色 + 粉色随机
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

        # 获取屏幕尺寸
        geo = QApplication.primaryScreen().geometry()
        screen_w = float(geo.width())
        screen_h = float(geo.height())

        count = random.randint(*self._config['count_range'])
        return [
            WhitePinkCollisionParticle(cx, cy, screen_w, screen_h, self._config)
            for _ in range(count)
        ]


class WhitePinkCollisionParticle:
    """单个白粉色碰撞反弹方形粒子"""

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
