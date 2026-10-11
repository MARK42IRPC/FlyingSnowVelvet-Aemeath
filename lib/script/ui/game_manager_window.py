"""游戏列表管理器窗口（产品侧再导出垫片）。

`GameManagerWindow` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/game_manager_window.py`。本模块按原名再导出，
`game_runtime` / `builtin_pages` 与测试的既有导入面不变。

宿主不得 import `lib.script`，因此游戏包服务单例由本垫片在导入时经 seam 钩子登记：
钩子按名字取本模块的模块级 `get_game_package_service`，因此
`patch("lib.script.ui.game_manager_window.get_game_package_service")` 这一既有补丁点照旧生效。
本模块不再 `import PyQt5`，冻结清单里不再有它。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def get_game_package_service():
    from lib.script.gemes.MAIN.game_packages import (
        get_game_package_service as _get,
    )

    return _get()


def _package_service():
    """宿主取游戏包服务的入口：按名字取本模块级函数（保留补丁点）。"""
    from lib.script.ui import game_manager_window as _module

    return _module.get_game_package_service()


render_bridge.install_tier_d_seams(ui_font=render_bridge.ui_font, digit_font=render_bridge.digit_font)
render_bridge.install_tier_d_hook("game_manager_window:package_service", _package_service)

(GameManagerWindow,) = render_bridge.tier_d_reexports("game_manager_window", ("GameManagerWindow",))

__all__ = ["GameManagerWindow"]
