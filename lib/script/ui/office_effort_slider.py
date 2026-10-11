"""办公页「推理强度五档滑条」（产品侧再导出垫片）。

`OfficeEffortSlider` 与 `effort_level_color` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/office_effort_slider.py`。这里按原名再导出；档位阶梯
（值 + 文案）与缺省档经 `tier_d_seams` 由本垫片注入。
"""

from __future__ import annotations

from lib.script.office.contracts import DEFAULT_REASONING_EFFORT, REASONING_EFFORTS
from lib.script.ui import render_bridge

#: 档位文案与 `REASONING_EFFORTS` 一一对应，顺序即强度顺序：极速 → 沉思。
EFFORT_LABELS = ("极速", "轻量", "一般", "思考", "沉思")
EFFORT_LEVELS = tuple(zip(REASONING_EFFORTS, EFFORT_LABELS))
EFFORT_TICK_COUNT = len(EFFORT_LEVELS)


def _office_effort_levels():
    return EFFORT_LEVELS


def _office_default_effort():
    return DEFAULT_REASONING_EFFORT


render_bridge.install_tier_d_hook("office_effort_levels", _office_effort_levels)
render_bridge.install_tier_d_hook("office_default_effort", _office_default_effort)

(OfficeEffortSlider, effort_level_color) = render_bridge.tier_d_reexports(
    "office_effort_slider", ("OfficeEffortSlider", "effort_level_color")
)

__all__ = [
    "EFFORT_LABELS",
    "EFFORT_LEVELS",
    "EFFORT_TICK_COUNT",
    "OfficeEffortSlider",
    "effort_level_color",
]
