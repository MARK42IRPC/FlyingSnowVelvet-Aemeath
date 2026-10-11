"""工作台「办公模式」页（产品侧装配垫片）。

`OfficeModePage` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/office_mode_page.py`。本模块按原名再导出页面类，并保留
原先的模块级入口函数（`open_office_page` / `list_office_skills` / ...）供既有导入面继续使用；
这些函数本体就是宿主经共享 seam 取用的产品侧实现，因此同时注册为 seam 注入物。

注入物一律是**惰性解析器或本模块函数的直接引用**：每次调用都重新读本模块属性，测试对垫片
属性的 patch 照旧生效。
"""

from __future__ import annotations

from lib.script.ui import render_bridge
# 注入物：宿主经这些模块属性惰性解析（`noqa: F401` 因为本模块只是装配垫片）。
from lib.script.ui.ai_settings_defaults import AI_DEFAULT_VALUES  # noqa: F401
from lib.script.ui.ai_settings_storage import (  # noqa: F401
    load_ai_values,
    save_office_values,
)


def open_office_page() -> None:
    """打开（或复用）独立办公页面；延迟导入，避免办公窗口反向依赖这一页。"""
    from lib.script.ui.office_page import open_office_window

    open_office_window()


def list_office_skills() -> list:
    from lib.script.office import skills

    return skills.list_skills()


def install_office_skill(source):
    from lib.script.office import skills

    return skills.install_skill(source)


def remove_office_skill(name: str):
    from lib.script.office import skills

    return skills.remove_skill(name)


def list_office_plugins() -> list:
    from lib.script.office import plugins

    return plugins.list_plugins()


def install_office_plugin(source):
    from lib.script.office import plugins

    return plugins.install_plugin(source)


def remove_office_plugin(name: str):
    from lib.script.office import plugins

    return plugins.remove_plugin(name)


def _office_skill_admin():
    """技能管理三件套；每次调用重新读本模块属性，测试 patch 照旧生效。"""
    from lib.script.ui import office_mode_page as shim

    return (shim.list_office_skills, shim.install_office_skill, shim.remove_office_skill)


def _office_plugin_admin():
    from lib.script.ui import office_mode_page as shim

    return (shim.list_office_plugins, shim.install_office_plugin, shim.remove_office_plugin)


def _office_open_page():
    from lib.script.ui import office_mode_page as shim

    shim.open_office_page()


def _office_defaults():
    from lib.script.ui import office_mode_page as shim

    return dict(shim.AI_DEFAULT_VALUES)


def _office_storage_save(values, defaults):
    from lib.script.ui import office_mode_page as shim

    shim.save_office_values(values, defaults)


render_bridge.install_tier_d_hook("office_open_page", _office_open_page)
render_bridge.install_tier_d_hook("office_skill_admin", _office_skill_admin)
render_bridge.install_tier_d_hook("office_plugin_admin", _office_plugin_admin)
render_bridge.install_tier_d_hook("ai_settings_defaults", _office_defaults)
render_bridge.install_tier_d_hook("office_storage_save", _office_storage_save)
render_bridge.install_tier_d_hook("ai_storage_load", load_ai_values)


def _office_effort_steps() -> int:
    from lib.script.office.contracts import REASONING_EFFORTS

    return len(REASONING_EFFORTS)


render_bridge.install_tier_d_hook("office_effort_steps", _office_effort_steps)

(OfficeModePage, OPEN_OFFICE_HINT, SAVE_STATUS_HINT) = render_bridge.tier_d_reexports(
    "office_mode_page", ("OfficeModePage", "OPEN_OFFICE_HINT", "SAVE_STATUS_HINT")
)

__all__ = [
    "OfficeModePage",
    "OPEN_OFFICE_HINT",
    "SAVE_STATUS_HINT",
    "open_office_page",
    "list_office_skills",
    "install_office_skill",
    "remove_office_skill",
    "list_office_plugins",
    "install_office_plugin",
    "remove_office_plugin",
]
