"""故障跟踪窗口的水印覆盖层（产品侧再导出垫片）。

`_BugTrackerWatermarkOverlay` 是纯 Qt 控件，已下沉到档位 D 宿主
`lib/core/render/backends/qt/widgets/bug_tracker_widgets.py`：它只做「读宿主窗口身上的
`_watermark_*` 文本 + 按 `QPainter` 画上去」这类控件工具包事实，不看窗口状态。

本模块不再是 `QWidget` 实现、不再 `import PyQt5`：类名与字体注入都经 `render_bridge`
转发（档位 D 要求控件只向 bridge 要工具包能力，与 `workbench_widgets.py` / `forum_board.py`
同形）。`bug_tracker_window.py` 的导入面与调用点零改动。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

_BugTrackerWatermarkOverlay = render_bridge.bug_tracker_watermark_overlay_class()

#: 与下沉前逐字段同源；`bug_tracker_widgets` 因此不必反向 import 产品面。
render_bridge.configure_bug_tracker_watermark_font(render_bridge.digit_font)

__all__ = [
    "_BugTrackerWatermarkOverlay",
]
