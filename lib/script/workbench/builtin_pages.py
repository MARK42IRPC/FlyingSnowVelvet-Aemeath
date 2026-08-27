"""Built-in lazy page factories for the unified workbench."""
from __future__ import annotations

from importlib import import_module

from lib.script.workbench.page_registry import WorkbenchPageSpec, default_page_spec


def _create_bug_tracker_page():
    from lib.script.bug_tracker.window import BugTrackerWindow

    return BugTrackerWindow(embedded=True)


def _create_game_manager_page():
    from lib.script.gemes.MAIN.manager_window import GameManagerWindow
    from lib.script.gemes.MAIN.runtime import get_game_runtime

    return GameManagerWindow(get_game_runtime(), embedded=True)


def _create_office_page():
    module = import_module("lib.script.ui.office_page")
    return module.OfficeWorkbenchPage(embedded=True)


def builtin_tool_page_specs() -> tuple[WorkbenchPageSpec, ...]:
    """Return the single registration source for built-in maintenance tools."""
    return (
        default_page_spec("office", factory=_create_office_page),
        default_page_spec("game_manager", factory=_create_game_manager_page),
        default_page_spec("bug_tracker", factory=_create_bug_tracker_page),
    )
