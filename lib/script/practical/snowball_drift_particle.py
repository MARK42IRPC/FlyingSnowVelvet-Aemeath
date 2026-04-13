"""雪球落地溅雪粒子 - snow_drift 的轻量化版本

相比 snow_drift（雪堆触发的落雪堆积效果）：
  - 粒子数量减半（count_range 减半）
  - 寿命减半（life_decay_settled 翻倍）
  - 无屏幕边界碰撞反弹，粒子触底后直接静止消退
  - 去掉堆积阶段的"触底精确贴地"，改为在落地位置快速消退

用于雪球弹跳落地时的轻量溅射效果。
"""
import random
from typing import Tuple

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui     import QColor

from lib.script.practical.base_particle import BaseParticleScript
from lib.core.plugin_registry import register_particle


@register_particle("snowball_drift")
class SnowballDriftParticleScript(BaseParticleScript):
    """
    雪球落地溅射粒子脚本（轻量化，无边界碰撞）。

    粒子从触发点向下溅射飘落，触底后快速消退。
    """

    PARTICLE_ID = "snowball_drift"

    def __init__(self):
        super().__init__()
        self._config = {
            'count_range':        (2, 4),            # snow_drift 的一半：(4,8) → (2,4)
            'radius_range':       (1, 4),            # 与 snow_drift 相同
            'vx_range':           (-1.5, 1.5),       # 与 snow_drift 相同
            'vy_range':           (1.5, 3.5),        # 与 snow_drift 相同
            'drift_noise':        0.25,              # 与 snow_drift 相同
            'gravity':            0.06,              # 与 snow_drift 相同
            'drag':               0.99,              # 与 snow_drift 相同
            'life_decay_settled': 0.006,             # snow_drift 的两倍：0.003 → 0.006（寿命减半 ≈ 2.7s）
            'color':              QColor(255, 255, 255),
            'ground_margin':      6,                 # 与 snow_drift 相同
        }

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        """在指定位置生成落雪溅射粒子，统一以中心点作为发射源。"""
        if area_type == 'circle':
            cx, cy, _ = area_data
        elif area_type == 'rect':
            x1, y1, x2, y2 = area_data
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        else:
            cx, cy = area_data[0], area_data[1]

        screen_h = QApplication.primaryScreen().geometry().height()
        ground_y = float(screen_h - self._config['ground_margin'])

        count = random.randint(*self._config['count_range'])
        return [
            SnowballDriftParticle(cx, cy, ground_y, self._config)
            for _ in range(count)
        ]


class SnowballDriftParticle:
    """
    单个雪球落地溅射粒子（无边界碰撞，触底后快速消退）。
    """

    is_circle = True  # 渲染层使用 drawEllipse

    def __init__(self, x: float, y: float, ground_y: float, config: dict):
        self.x = float(x)
        self.y = float(y)

        self.vx = random.uniform(*config['vx_range'])
        self.vy = random.uniform(*config['vy_range'])

        self.size    = random.randint(*config['radius_range'])
        self.color   = config['color']
        self.gravity = config['gravity']
        self.drag    = config['drag']

        self._drift_noise = config['drift_noise']

        self.life     = 1.0
        self.max_life = 1.0
        self._life_decay_settled = config['life_decay_settled']

        self._ground_y = ground_y
        self._settled  = False  # False=下落中，True=已触底消退

    def update(self):
        if self._settled:
            # ── 触底阶段：位置固定，快速消退 ─────────────────────────
            self.life -= self._life_decay_settled
        else:
            # ── 下落阶段：水平扰动 + 重力 + 阻力（无边界碰撞）───────
            self.vx += random.uniform(-self._drift_noise, self._drift_noise)
            self.vx *= self.drag
            self.vy += self.gravity
            self.vy *= self.drag

            self.x += self.vx
            self.y += self.vy

            # 触底检测
            if self.y + self.size >= self._ground_y:
                self.y     = self._ground_y - self.size
                self.vx    = 0.0
                self.vy    = 0.0
                self._settled = True
                self.life  = 1.0  # 重置后开始消退计时

    @property
    def alive(self) -> bool:
        return self.life > 0
