"""工作台共享控件（产品侧再导出垫片）。

`WorkbenchPetAboutButton` / `WorkbenchOverviewPage` / `paint_speaker_action_button` 已按
《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/workbench_components.py`。本模块按原名再导出，既有导入面
不变。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    WorkbenchOverviewPage,
    WorkbenchPetAboutButton,
    paint_speaker_action_button,
) = render_bridge.tier_d_reexports(
    "workbench_components",
    ("WorkbenchOverviewPage", "WorkbenchPetAboutButton", "paint_speaker_action_button"),
)

(create_window_button,) = render_bridge.tier_d_reexports("window_buttons", ("create_window_button",))

# 动作按钮底壳在 `paintEvent` 里经 `tier_d_seams` 取绘制宿主；绘制实现与字体必须先注入，
# 否则缺省 `create_draw_backend()` 会抛异常、在 Qt 虚函数回调里表现为 0xC0000409 崩溃。
render_bridge.install_tier_d_seams(
    draw_backend=render_bridge.create_draw_backend,
    color=render_bridge.qt_color,
    color_name=render_bridge.qt_color_name,
)

__all__ = [
    "WorkbenchOverviewPage",
    "WorkbenchPetAboutButton",
    "create_window_button",
    "paint_speaker_action_button",
]
