"""工作台自绘件：淡入淡出遮罩与明暗主题开关（产品侧再导出垫片）。

这两个自绘小件已下沉到档位 D 宿主
`lib/core/render/backends/qt/widgets/workbench_widgets.py`：它们只依赖尺寸助手与工作台
主题色，不看窗口状态，属于「需要跨文件共享的 Qt 宿主」。`workbench_window.py` 与本模块的
既有导入面因此保持不变。

本模块不再是 `QWidget` 实现，也不再 `import PyQt5`：它只把名字经 `render_bridge` 转发
（档位 D 要求控件只向 bridge 要工具包能力），冻结清单里不再有它。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    _WorkbenchFadeOverlay,
    _WorkbenchThemeToggle,
) = render_bridge.workbench_overlay_classes()

__all__ = [
    "_WorkbenchFadeOverlay",
    "_WorkbenchThemeToggle",
]
