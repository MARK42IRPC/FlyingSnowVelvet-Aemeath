"""统一工作台窗口（产品侧再导出垫片）。

`WorkbenchWindow` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/workbench_window.py`。本模块按原名再导出，
`qt_application_ui` / `tray_icon` 与测试的既有导入面不变。

宿主不得 import `lib.script`：页面规约、主题样式表、分组顺序与配置保存入口由本垫片在导入时经
seam 钩子登记；本模块不再 `import PyQt5`，冻结清单里不再有它。
"""

from __future__ import annotations

from pathlib import Path

from lib.script.ui import render_bridge


def _page_registry_primitives():
    from lib.script.workbench.page_registry import (
        WorkbenchPageRegistry,
        WorkbenchPageSpec,
        default_page_spec,
    )

    return WorkbenchPageRegistry, WorkbenchPageSpec, default_page_spec


def _control_panel_page_metadata():
    from lib.script.workbench.settings import GENERAL_CONFIG_CATEGORIES

    return [("ai", "AI 设置")] + [
        (spec.page_id, spec.tab_title) for spec in GENERAL_CONFIG_CATEGORIES
    ]


def _stylesheet(mode=None):
    from lib.script.workbench.theme import workbench_stylesheet

    return workbench_stylesheet(mode)


def _save_general_values(values):
    from config.general_user_settings import save_general_values

    return save_general_values(values)


def _product_module():
    """产品垫片模块本身：宿主按名字取被补丁的模块级名字。"""
    from lib.script.ui import workbench_window as _module

    return _module


render_bridge.install_tier_d_seams(ui_font=render_bridge.ui_font)
render_bridge.install_tier_d_hook("workbench_window:product_module", _product_module)
render_bridge.install_tier_d_hook("workbench_window:page_registry", _page_registry_primitives)
render_bridge.install_tier_d_hook(
    "workbench_window:control_panel_metadata", _control_panel_page_metadata
)
render_bridge.install_tier_d_hook("workbench_window:stylesheet", _stylesheet)
render_bridge.install_tier_d_hook(
    "workbench_window:save_general_values", _save_general_values
)

(
    WorkbenchWindow,
    QSettings,
    Event,
    EventType,
) = render_bridge.tier_d_reexports(
    "workbench_window", ("WorkbenchWindow", "QSettings", "Event", "EventType")
)

#: 既有测试取用的模块级常量（导航字号倍率与工作台图标路径）。
_NAV_FONT_SCALE = 1.25
_WORKBENCH_ICON_PATH = Path(__file__).resolve().parents[3] / "resc" / "icon.ico"

__all__ = ["Event", "EventType", "QSettings", "WorkbenchWindow"]
