"""CMD 窗口的共享助手与自绘小件（产品侧再导出垫片）。

`_hex` / `_strip_ansi` / `_hit_edge` / `_EDGE_CURSORS` / `_StreamLineEvent` /
`_StreamDoneEvent` / `_TitleButton` / `_CloseButton` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/cmd_window_widgets.py`。本模块按原名再导出，既有导入面
不变；颜色与字体入口由 `tier_d_seams` 注入。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    _CloseButton,
    _EDGE_CURSORS,
    _StreamDoneEvent,
    _StreamLineEvent,
    _TitleButton,
    _hex,
    _hit_edge,
    _strip_ansi,
) = render_bridge.tier_d_reexports("cmd_window_widgets", (
    "_CloseButton",
    "_EDGE_CURSORS",
    "_StreamDoneEvent",
    "_StreamLineEvent",
    "_TitleButton",
    "_hex",
    "_hit_edge",
    "_strip_ansi",
))

# 标题按钮的 QSS / 自绘颜色经 `tier_d_seams.color` 取；绘制实现与字体入口必须先注入，
# 否则 `create_draw_backend()` 缺省会抛异常。
render_bridge.install_tier_d_seams(
    draw_backend=render_bridge.create_draw_backend,
    color=render_bridge.qt_color,
    color_name=render_bridge.qt_color_name,
)

__all__ = [
    "_CloseButton",
    "_EDGE_CURSORS",
    "_StreamDoneEvent",
    "_StreamLineEvent",
    "_TitleButton",
    "_hex",
    "_hit_edge",
    "_strip_ansi",
]
