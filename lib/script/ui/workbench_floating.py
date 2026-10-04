"""Workbench floating-window chrome (product re-export shim).

浮窗外壳已收敛到渲染层：样式表与主题变更判定是后端中立数据，落在
``lib/core/render/visuals/workbench_chrome.py``；真实的 ``QWidget`` 宿主能力落在
``lib/core/render/backends/qt/widgets/floating_window.py``。

本模块只保留既有导入名（``WorkbenchFloatingWindow`` / ``floating_window_stylesheet`` /
``is_workbench_theme_change`` 等），让 ``lib/script/ui`` 的浮窗与测试继续按原路径取用；
新代码请直接引用宿主类。
"""

from __future__ import annotations

from lib.core.render.backends.qt.widgets.floating_window import (
    FloatingDragFilter,
    FloatingWindowThemeWatcher,
    QtWorkbenchFloatingWindow,
)
from lib.core.render.backends.qt.widgets.window_buttons import create_window_button
from lib.core.render.visuals.workbench_chrome import (
    FLOATING_WINDOW_OBJECT_NAME,
    floating_window_stylesheet,
    is_workbench_theme_change,
)

#: 历史名：Qt 时代的浮窗基类；新代码请用 ``QtWorkbenchFloatingWindow``。
WorkbenchFloatingWindow = QtWorkbenchFloatingWindow


__all__ = [
    "FLOATING_WINDOW_OBJECT_NAME",
    "FloatingDragFilter",
    "FloatingWindowThemeWatcher",
    "QtWorkbenchFloatingWindow",
    "WorkbenchFloatingWindow",
    "create_window_button",
    "floating_window_stylesheet",
    "is_workbench_theme_change",
]
