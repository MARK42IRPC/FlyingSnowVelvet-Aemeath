"""浮窗外壳门面：历史导入名的再导出垫片。

浮窗外壳本身已收敛到渲染层：样式表与主题变更判定是后端中立数据，落在
``lib/core/render/visuals/workbench_chrome.py``；真实的 ``QWidget`` 宿主能力落在
``lib/core/render/backends/qt/widgets/floating_window.py``。

本模块只保留既有导入名（``WorkbenchFloatingWindow`` / ``floating_window_stylesheet`` /
``is_workbench_theme_change`` 等），让 ``lib/script/ui`` 的浮窗与测试继续按原路径取用。
它不再直接 import ``lib.core.render.backends.qt``：浮窗基类与窗眉按钮工厂经
``render_bridge`` 转发，这正是档位 D 要求的"控件只问 bridge 要工具包能力"。

``WorkbenchFloatingWindow`` 是产品浮窗的基类，必须在**类定义时**就能取到，因此不能在
模块级只放一个 ``None`` 占位；这里按产品浮窗导入本模块的时机，在末尾显式解析一次并
绑定真实类。
"""

from __future__ import annotations

from lib.core.render.visuals.workbench_chrome import (
    FLOATING_WINDOW_OBJECT_NAME,
    floating_window_stylesheet,
    is_workbench_theme_change,
)
from lib.script.ui import render_bridge


def create_floating_window_base():
    """浮窗外壳的 Qt 基类（经 ``render_bridge`` 转发，档位 D 的控件侧落点）。"""
    return render_bridge.create_floating_window_base()


def create_window_button(parent, standard_icon, tooltip, callback, *, danger: bool = False):
    """创建一枚窗眉控制按钮（经 ``render_bridge`` 转发）。"""
    return render_bridge.create_window_button(
        parent, standard_icon, tooltip, callback, danger=danger
    )


(
    FloatingDragFilter,
    FloatingWindowThemeWatcher,
    QtWorkbenchFloatingWindow,
) = render_bridge.floating_window_classes()

#: 历史名：Qt 时代的浮窗基类；新代码请用 ``QtWorkbenchFloatingWindow``。
WorkbenchFloatingWindow = QtWorkbenchFloatingWindow


__all__ = [
    "FLOATING_WINDOW_OBJECT_NAME",
    "FloatingDragFilter",
    "FloatingWindowThemeWatcher",
    "QtWorkbenchFloatingWindow",
    "WorkbenchFloatingWindow",
    "create_floating_window_base",
    "create_window_button",
    "floating_window_stylesheet",
    "is_workbench_theme_change",
]
