"""右键 UI 的单一顶层窗口（产品侧再导出垫片）。

`RightClickUiLayer` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/right_click_ui_layer.py`。本模块按原名再导出，既有导入面
不变；呈现几何入口由本垫片在导入时注入（`tier_d_seams.install_presentation_host`）。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

render_bridge.install_tier_d_presentation_host(render_bridge.presentation_host)

(RightClickUiLayer,) = render_bridge.tier_d_reexports(
    "right_click_ui_layer", ("RightClickUiLayer",)
)

__all__ = ["RightClickUiLayer"]
