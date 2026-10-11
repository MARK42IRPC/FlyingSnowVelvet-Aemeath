"""游戏运行时宿主窗口与其几何助手（产品侧再导出垫片）。

`GameRuntimePanel` 与两个纯几何函数 `centered_aspect_rect` / `aspect_resize_geometry` 已按
《render 层边界契约》第 3 节下沉到 `lib/core/render/backends/qt/widgets/game_runtime_panel.py`。
本模块按原名再导出，`game_runtime.py` 与 `tests/test_game_runtime_geometry.py` 的既有导入面不变。

宿主不得 import `lib.script`，因此组件图层工厂（`create_component_layer` /
`create_component_layer_request`）由本垫片在导入时经 seam 钩子登记为产品侧 `render_bridge`；
屏幕几何与字体走共享 seam。本模块不再 `import PyQt5`，冻结清单里不再有它。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def _game_runtime_panel_render_bridge():
    return render_bridge


render_bridge.install_tier_d_seams(
    draw_backend=render_bridge.create_draw_backend,
)
render_bridge.install_tier_d_hook(
    "game_runtime_panel_render_bridge", _game_runtime_panel_render_bridge
)

(
    GameRuntimePanel,
    centered_aspect_rect,
    aspect_resize_geometry,
) = render_bridge.tier_d_reexports(
    "game_runtime_panel",
    ("GameRuntimePanel", "centered_aspect_rect", "aspect_resize_geometry"),
)

__all__ = ["GameRuntimePanel", "centered_aspect_rect", "aspect_resize_geometry"]
