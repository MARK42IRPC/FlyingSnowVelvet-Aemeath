"""Standalone bug tracker window（产品侧再导出垫片）。

`BugTrackerWindow` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/bug_tracker_window.py`。本模块按原名再导出，
`lib/script/bug_tracker/__main__.py` / `builtin_pages` 与测试的既有导入面不变。

宿主不得 import `lib.script`：日志存储、水印载荷、数据混入与水印覆盖层由本垫片在导入时经
seam 钩子登记。主题色仍由模块级名字承载（供测试取用），因此导入后从宿主同步一次。
本模块不再 `import PyQt5`，冻结清单里不再有它。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def _data_mixin():
    from lib.script.ui.bug_tracker_data import BugTrackerDataMixin

    return BugTrackerDataMixin


def _log_store():
    from lib.script.bug_tracker.storage import BugTrackerLogStore

    return BugTrackerLogStore()


def _watermark_payload():
    from lib.script.app.startup_probe import load_saved_watermark_payload

    return load_saved_watermark_payload


render_bridge.install_tier_d_seams(ui_font=render_bridge.ui_font)
render_bridge.install_tier_d_hook("bug_tracker_window:data_mixin", _data_mixin)
render_bridge.install_tier_d_hook("bug_tracker_window:log_store", _log_store)
render_bridge.install_tier_d_hook("bug_tracker_window:watermark_payload", _watermark_payload)

(BugTrackerWindow,) = render_bridge.tier_d_reexports(
    "bug_tracker_window", ("BugTrackerWindow",)
)

#: 主题色全局：宿主在 `_set_theme_colors()` / `refresh_workbench_theme()` 里刷新自己的模块全局，
#: 这里同步一份，保留 `window_module._DANGER` 这类既有取用点。
for _name in (
    "_BG", "_HEADER_BG", "_PANEL_BG", "_BORDER", "_SOFT_BORDER", "_MID", "_PINK",
    "_PINK_SOFT", "_CYAN", "_WARNING", "_DANGER", "_TEXT_MAIN", "_TEXT_SOFT",
    "_TEXT_DIM", "_BLACK",
):
    globals()[_name] = getattr(render_bridge.tier_d_reexports_module("bug_tracker_window"), _name)
del _name

__all__ = ["BugTrackerWindow"]
