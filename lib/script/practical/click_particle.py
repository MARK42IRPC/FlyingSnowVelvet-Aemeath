"""文字粒子特效 - click（浅青色）

每次触发生成 3 个"Click"文字粒子（拉海洛字体，首字母大写），
向随机方向喷出后线性减速，生命结束时速度恰好降为 0，0.5~0.8s 后淡出消失。
"""
from __future__ import annotations

import math
import random
from typing import Tuple

from config.font_config import get_digit_font_family
from config.scale import scale_px
from lib.core.graphics.types import Color, FontSpec
from lib.script.practical.base_particle import BaseParticleScript, per_second_delta, tick_seconds
from lib.script.plugin_registry import register_particle

# ── 配色 ──────────────────────────────────────────────────────────────
_COLOR_CYAN = Color(173, 216, 230)   # 浅青色


@register_particle("click")
class ClickParticleScript(BaseParticleScript):
    """文字粒子 'Click'：随机方向飞出，拉海洛字体，浅青色，无重力。"""

    PARTICLE_ID = "click"

    def __init__(self):
        super().__init__()
        self._config = {
            'text':          'Click',
            'color':         _COLOR_CYAN,
            'count':         3,              # 每次固定生成 3 个
            'font_px_range': (scale_px(8, min_abs=1), scale_px(22, min_abs=1)),  # 像素字号随机范围
            'speed_range':   (120, 220),     # 初始速度范围 px/s（提高后减速到 0）
            'life_range':    (0.5, 0.8),     # 寿命范围 s
        }

    def create_particles(self, area_type: str, area_data: Tuple) -> list:
        # 无论区域类型，均以中心点为生成原点
        if area_type == 'rect':
            x1, y1, x2, y2 = area_data
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        elif area_type == 'circle':
            cx, cy, _ = area_data
        else:  # point
            cx, cy = area_data

        family = get_digit_font_family()
        cfg = self._config
        return [ClickParticle(cx, cy, cfg, family) for _ in range(cfg['count'])]


class ClickParticle:
    """单个 Click 文字粒子：随机方向、随机字号、线性减速至 0，淡出消失。"""

    def __init__(self, x: float, y: float, config: dict, font_family: str):
        self.x = float(x)
        self.y = float(y)
        self.is_text = True
        self.text  = config['text']
        self.color = config['color']

        # 字体：随机像素字号，避免不同设备 DPI 下尺寸异常
        px = random.randint(*config['font_px_range'])
        self.font = FontSpec(font_family, px)

        # 初始速度向量（存为 vx0/vy0，update 按剩余生命比例缩放）
        angle = random.uniform(0.0, math.tau)
        speed = per_second_delta(random.uniform(*config['speed_range']))
        self.vx0 = math.cos(angle) * speed
        self.vy0 = math.sin(angle) * speed

        # 生命周期
        self.max_life = random.uniform(*config['life_range'])
        self.life     = self.max_life

    def update(self) -> None:
        ratio = self.life / self.max_life
        self.x += self.vx0 * ratio
        self.y += self.vy0 * ratio
        self.life -= tick_seconds()

    @property
    def alive(self) -> bool:
        return self.life > 0
