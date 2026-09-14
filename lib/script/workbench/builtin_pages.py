"""Built-in lazy page factories for the unified workbench."""
from __future__ import annotations

from importlib import import_module

from lib.script.workbench.page_registry import WorkbenchPageSpec, default_page_spec


def _create_bug_tracker_page():
    module = import_module("lib.script.ui.bug_tracker_window")

    return module.BugTrackerWindow(embedded=True)


def _create_game_manager_page():
    window_module = import_module("lib.script.ui.game_manager_window")
    runtime_module = import_module("lib.script.gemes.MAIN.runtime")

    return window_module.GameManagerWindow(runtime_module.get_game_runtime(), embedded=True)


def _create_office_page():
    # 工作台只放办公配置；任务界面是独立窗口（配置页按钮与托盘入口打开）。
    module = import_module("lib.script.ui.office_mode_page")
    return module.OfficeModePage(embedded=True)


def builtin_tool_page_specs() -> tuple[WorkbenchPageSpec, ...]:
    """Return the single registration source for built-in maintenance tools."""
    return (
        default_page_spec("office", factory=_create_office_page),
        default_page_spec("game_manager", factory=_create_game_manager_page),
        default_page_spec("bug_tracker", factory=_create_bug_tracker_page),
    )
