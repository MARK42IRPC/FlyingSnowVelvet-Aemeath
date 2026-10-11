"""AI 设置面板 tabs 组装辅助（产品垫片）。

标签栏控件与整组装配函数已按《render 层边界契约》第 3 节的档位 D 下沉到
`lib/core/render/backends/qt/widgets/ai_settings_tabs.py`。这里按原名再导出，既有导入面不变；
绘制后端与主题色入口由 `render_bridge.install_tier_d_seams()` 注入。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    TabBarWidget,
    attach_ai_settings_tabs,
    layout_ai_settings_tab_bar,
    show_ai_settings_tab_bar,
    hide_ai_settings_tab_bar,
    layout_ai_settings_tab_panels,
    set_active_ai_settings_tab,
) = render_bridge.tier_d_reexports("ai_settings_tabs", (
    "TabBarWidget",
    "attach_ai_settings_tabs",
    "layout_ai_settings_tab_bar",
    "show_ai_settings_tab_bar",
    "hide_ai_settings_tab_bar",
    "layout_ai_settings_tab_panels",
    "set_active_ai_settings_tab",
))

render_bridge.install_tier_d_seams(
    draw_backend=render_bridge.create_draw_backend,
    color=render_bridge.qt_color,
    color_name=render_bridge.qt_color_name,
)

__all__ = [
    "TabBarWidget",
    "attach_ai_settings_tabs",
    "hide_ai_settings_tab_bar",
    "layout_ai_settings_tab_bar",
    "layout_ai_settings_tab_panels",
    "set_active_ai_settings_tab",
    "show_ai_settings_tab_bar",
]
