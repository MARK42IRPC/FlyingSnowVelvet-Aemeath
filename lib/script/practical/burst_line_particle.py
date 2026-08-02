"""爆发线条粒子特效

以中心点为原点，向四周均匀发射 6-8 条彩色线条。
每条线条：
  - 颜色：白色 / 亮粉色 / 亮蓝色（随机）
  - 宽度：1~2 像素
  - 长度：10~14 px，随寿命线性缩短至 0
  - 不受重力，诞生后匀减速直到停止，无淡出
  - 线条从粒子当前位置沿指向中心方向延伸
"""

from __future__ import annotations

import math
import random
from typing import List

from lib.core.graphics.types import Color
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta
from lib.core.plugin_registry import register_particle


# ── 调色板（白色 / 亮粉色 #FECBE5 / 亮蓝色 #BFF3FE）─────────────────────────────────────
_COLORS: List[Color] = [
    Color(255, 255, 255),   # 白色
    Color(254, 203, 229),   # 亮粉色 #FECBE5
    Color(191, 243, 254),   # 亮蓝色 #BFF3FE
]


# ══════════════════════════════════════════════════════════════════════
# 粒子数据体
# ══════════════════════════════════════════════════════════════════════

class BurstLineParticle:
    """单个爆发线条粒子。

    从中心点向外发射，每 tick 沿发射方向反向（指向中心）绘制固定宽度线段。
    线段长度随寿命线性缩短至 0。
    """

    is_line: bool = True   # 告知渲染器使用线条绘制路径
    no_fade: bool = True   # 不做透明度淡出（alpha 始终 255）

    def __init__(
        self,
        cx: float,
        cy: float,
        angle: float,
        config: dict,
    ) -> None:
        """
        Args:
            cx, cy : 爆发中心（覆盖层本地坐标）
            angle  : 发射方向（弧度，0=右，逆时针为正）
            config : 粒子配置字典
        """
        # 中心坐标（不变）
        self.cx: float = cx
        self.cy: float = cy

        # 当前位置（从中心出发向外运动）
        self.x: float = cx
        self.y: float = cy

        # 初始速度（沿 angle 方向向外）
        speed = per_second_delta(random.uniform(*config['speed_range']))
        self.vx: float = math.cos(angle) * speed
        self.vy: float = math.sin(angle) * speed

        # 线条指向中心的单位向量（固定，避免每帧除法）
        self.line_dx: float = -math.cos(angle)
        self.line_dy: float = -math.sin(angle)

        # 线条外观
        self.max_length: int   = random.randint(*config['length_range'])
        self.pen_width: int    = random.randint(*config['width_range'])
        self.color: Color      = random.choice(config['colors'])

        # 减速系数
        self._drag: float = float(config['drag'])

        # 生命值
        self.max_life: float  = 1.0
        self.life: float      = 1.0
        self._life_decay: float = float(config['life_decay'])

    @property
    def length(self) -> float:
        """随寿命线性缩短的线条长度（px）。"""
        return self.max_length * (self.life / self.max_life)

    def update(self) -> None:
        # 施加阻力（匀减速至停止，无重力）
        self.vx *= self._drag
        self.vy *= self._drag
        # 位移
        self.x += self.vx
        self.y += self.vy
        # 寿命衰减
        self.life -= self._life_decay

    @property
    def alive(self) -> bool:
        return self.life > 0.0


# ══════════════════════════════════════════════════════════════════════
# 粒子脚本
# ══════════════════════════════════════════════════════════════════════

@register_particle("burst_line")
class BurstLineParticleScript(BaseParticleScript):
    """爆发线条粒子脚本。

    单点触发，生成 6-8 条均匀分布的线条粒子（含整体随机旋转和
    每条小幅随机偏转，避免绝对对称的机械感）。
    """

    PARTICLE_ID = 'burst_line'

    def __init__(self) -> None:
        super().__init__()
        self._config = {
            'count_range':  (6, 8),         # 每次线条数量
            'speed_range':  (180.0, 360.0), # 初始速度（px/s）
            'drag':         0.681472,       # 每 tick 速度保留比例（约等于旧 0.88^3）
            'length_range': (10, 14),       # 线条最大长度（px）
            'width_range':  (1, 2),         # 线条宽度（px）
            'life_decay':   0.195,          # 每 tick 寿命衰减（约等于旧 0.065*3）
            'colors':       _COLORS,
        }
        self._request_options: dict = {}

    def set_request_options(self, options: dict) -> None:
        self._request_options = dict(options or {})

    def create_particles(self, area_type: str, area_data: tuple) -> list:
        """仅对 point 类型有意义；其他类型取区域中心点。"""
        if area_type == 'point':
            cx, cy = area_data
        elif area_type == 'rect':
            x1, y1, x2, y2 = area_data
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        elif area_type == 'circle':
            cx, cy = area_data[0], area_data[1]
        else:
            return []

        config = dict(self._config)
        palette_override = self._request_options.get("rgb")
        if palette_override is not None:
            custom = Color(*(max(0, min(255, int(v))) for v in palette_override))
            config['colors'] = [
                custom.lighter(145),
                custom.lighter(120),
                custom.darker(110),
            ]

        count = random.randint(*config['count_range'])
        # 均匀角度分布 + 整体随机旋转（每次效果略有不同）
        base_step = (2.0 * math.pi) / count
        offset = random.uniform(0.0, base_step)

        particles = []
        for i in range(count):
            # 每条再叠加小幅随机偏转，消除绝对对称感
            angle = offset + base_step * i + random.uniform(-0.12, 0.12)
            particles.append(BurstLineParticle(cx, cy, angle, config))

        return particles
