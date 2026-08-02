"""文字粒子特效 - pink_click（浅粉色）

与 click 完全相同，仅颜色替换为浅粉色（255, 182, 193）。
"""
from __future__ import annotations

from lib.core.graphics.types import Color
from lib.script.practical.click_particle import ClickParticleScript
from lib.core.plugin_registry import register_particle

# ── 配色 ──────────────────────────────────────────────────────────────
_COLOR_PINK = Color(255, 182, 193)   # 浅粉色


@register_particle("pink_click")
class PinkClickParticleScript(ClickParticleScript):
    """文字粒子 'Click'：随机方向飞出，拉海洛字体，浅粉色，无重力。"""

    PARTICLE_ID = "pink_click"

    def __init__(self):
        super().__init__()
        # 仅覆盖颜色，其余参数与 click 完全一致
        self._config = {**self._config, 'color': _COLOR_PINK}
