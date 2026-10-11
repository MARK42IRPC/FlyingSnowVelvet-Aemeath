"""系统托盘菜单 UI 组件（产品侧再导出垫片）。

`TrayContextMenu` 与 `_TrayMenuHintStyle` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/tray_menu.py`。本模块按原名再导出，既有导入面
不变；颜色 / 字体入口与「三项提示框」面板由本垫片在导入时注入。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

# 颜色与字体入口必须先注入：`_TrayMenuHintStyle` 在 `drawControl` / `_watermark_font`
# 里经 `tier_d_seams.color` / `tier_d_seams.digit_font` 取值，未注入会在 Qt 虚函数回调
# 里抛异常（表现为 0xC0000409 崩溃且输出丢失）。
render_bridge.install_tier_d_seams(
    draw_backend=render_bridge.create_draw_backend,
    color=render_bridge.qt_color,
    color_name=render_bridge.qt_color_name,
    ui_font=render_bridge.ui_font,
    digit_font=render_bridge.digit_font,
)


def _tooltip_panel():
    """托盘菜单弹出前收起「三项提示框」；面板是产品件，经 seam 钩子回取。"""
    from lib.script.ui.tooltip_panel import get_tooltip_panel

    return get_tooltip_panel()


render_bridge.install_tier_d_hook("tray_menu_tooltip_panel", _tooltip_panel)

(TrayContextMenu,) = render_bridge.tier_d_reexports("tray_menu", ("TrayContextMenu",))
(_TrayMenuHintStyle,) = render_bridge.tier_d_reexports("tray_menu", ("_TrayMenuHintStyle",))

__all__ = ["TrayContextMenu", "_TrayMenuHintStyle"]
