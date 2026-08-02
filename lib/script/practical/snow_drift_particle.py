"""落雪堆积粒子 - 从生成点向下飘落，落到屏幕底部后静止堆积，缓慢消退

生命周期：
  1. 下落阶段：life 固定为 1.0（始终全不透明），受重力 + 水平随机扰动影响飘落
  2. 堆积阶段：触底后 life 重置为 1.0，以 life_decay_settled 缓慢衰减
               → 粒子在屏幕底部保持可见约 5~6 秒后渐隐，形成积雪视觉效果
"""

import random

from lib.core.graphics.types import Color
from lib.core.screen_utils import get_virtual_screen_rect
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta
from lib.core.plugin_registry import register_particle


@register_particle("snow_drift")
class SnowDriftParticleScript(BaseParticleScript):
    """
    落雪堆积粒子脚本。

    粒子从触发点向下飘落，到达屏幕底部后静止，
    缓慢消退形成短暂的"积雪"视觉效果。
    """

    PARTICLE_ID = "snow_drift"

    def __init__(self):
        super().__init__()
        self._config = {
            'count_range':        (4, 8),
            'radius_range':       (1, 4),        # 粒子半径（像素）
            'vx_range':           (-90, 90),     # 初始水平漂移速度（px/s）
            'vy_range':           (90, 210),     # 初始垂直下落速度（正值=向下，px/s）
            'drift_noise':        15.0,          # 每 tick 水平随机扰动幅度（px/s）
            'gravity':            3.6,           # 轻微重力加速度（px/s per tick）
            'drag':               0.970299,      # 每 tick 空气阻力系数（约等于旧 0.99^3）
            'life_decay_settled': 0.009,         # 落地后每 tick 生命衰减（约等于旧 0.003*3）
            'color':              Color(255, 255, 255),  # 纯白色
            'ground_margin':      6,             # 距屏幕底部边缘的落地安全距离（像素）
        }

    def create_particles(self, area_type: str, area_data: tuple) -> list:
        """在指定位置生成落雪粒子，统一以中心点作为发射源。"""
        # 统一取中心点
        if area_type == 'circle':
            cx, cy, _ = area_data
        elif area_type == 'rect':
            x1, y1, x2, y2 = area_data
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        else:  # point
            cx, cy = area_data[0], area_data[1]

        # 地面 Y 坐标（覆盖层本地坐标与屏幕坐标一致）
        screen = get_virtual_screen_rect()
        ground_y = float(screen.height - self._config['ground_margin'])

        count = random.randint(*self._config['count_range'])
        return [
            SnowDriftParticle(cx, cy, ground_y, self._config)
            for _ in range(count)
        ]


class SnowDriftParticle:
    """
    单个下落堆积雪花粒子。

    渲染层通过 is_circle=True 识别为圆形，使用 drawEllipse 绘制；
    p.size 表示半径，与 SnowParticle 保持一致。
    """

    is_circle = True  # 通知渲染器使用 drawEllipse + 抗锯齿

    def __init__(self, x: float, y: float, ground_y: float, config: dict):
        self.x = float(x)
        self.y = float(y)

        self.vx = per_second_delta(random.uniform(*config['vx_range']))
        self.vy = per_second_delta(random.uniform(*config['vy_range']))

        # size 在圆形粒子中表示半径（与 snow_particle.py 保持一致）
        self.size    = random.randint(*config['radius_range'])
        self.color   = config['color']
        self.gravity = per_second_delta(config['gravity'])
        self.drag    = float(config['drag'])

        self._drift_noise = per_second_delta(config['drift_noise'])

        # 下落阶段：life 固定为 1.0，触底后重置为 1.0 再开始衰减
        self.life     = 1.0
        self.max_life = 1.0
        self._life_decay_settled = float(config['life_decay_settled'])

        self._ground_y  = ground_y
        self._settled   = False  # False=下落中，True=已落地堆积

    def update(self):
        if self._settled:
            # ── 堆积阶段：位置固定，仅缓慢消退 ──────────────────────
            self.life -= self._life_decay_settled
        else:
            # ── 下落阶段：水平扰动 + 重力 + 阻力 ────────────────────
            self.vx += random.uniform(-self._drift_noise, self._drift_noise)
            self.vx *= self.drag
            self.vy += self.gravity
            self.vy *= self.drag

            self.x += self.vx
            self.y += self.vy

            # 触底检测：粒子底部（圆心 + 半径）到达地面线时静止
            if self.y + self.size >= self._ground_y:
                self.y     = self._ground_y - self.size  # 精确贴地
                self.vx    = 0.0
                self.vy    = 0.0
                self._settled = True
                self.life  = 1.0   # 重置生命，开始堆积消退计时

    @property
    def alive(self) -> bool:
        return self.life > 0
