"""系统托盘图标模块（产品侧再导出垫片）。

`TrayIcon` 与全局单例已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/tray_icon.py`。本模块按原名再导出，既有导入面不变
（`qt_backend_bootstrap` / `qt_application_ui` / 测试）。

宿主不得 import `lib.script`，因此托盘菜单类与各样产品入口留在这里、由宿主按名字取用；
`patch("lib.script.ui.tray_icon.TrayContextMenu")` / `patch("lib.script.ui.tray_icon.get_game_mode_service")`
等既有补丁点照旧生效。本模块不再 `import PyQt5`，冻结清单里不再有它。
"""

from __future__ import annotations

from pathlib import Path

from lib.script.ui import render_bridge

_host = render_bridge.tier_d_reexports_module("tray_icon")


def get_game_mode_service():
    from lib.script.app.game_mode_service import get_game_mode_service as _get

    return _get()


def TrayContextMenu(*args, **kwargs):
    from lib.script.ui.tray_menu import TrayContextMenu as _cls

    return _cls(*args, **kwargs)


def prepare_autostart_state(*args, **kwargs):
    from lib.script.app.tray_actions import prepare_autostart_state as _call

    return _call(*args, **kwargs)


def is_autostart_enabled(*args, **kwargs):
    from lib.script.app.autostart import is_autostart_enabled as _call

    return _call(*args, **kwargs)


def AISettingsPanel(*args, **kwargs):
    from lib.script.ui.ai_settings_panel import AISettingsPanel as _cls

    return _cls(*args, **kwargs)


def WorkbenchWindow(*args, **kwargs):
    from lib.script.ui.workbench_window import WorkbenchWindow as _cls

    return _cls(*args, **kwargs)


def builtin_tool_page_specs(*args, **kwargs):
    from lib.script.workbench.builtin_pages import builtin_tool_page_specs as _call

    return _call(*args, **kwargs)


def open_forum_window(*args, **kwargs):
    from lib.script.ui.forum_window import open_forum_window as _call

    return _call(*args, **kwargs)


def open_office_window(*args, **kwargs):
    from lib.script.ui.office_page import open_office_window as _call

    return _call(*args, **kwargs)


def _product_module():
    """产品侧装配模块本身：宿主按它取上面这些入口。"""
    from lib.script.ui import tray_icon as _module

    return _module


def _default_icon_path():
    """默认托盘图标路径：相对仓库根解析（宿主层级不可自算）。"""
    return Path(__file__).resolve().parents[3] / "resc" / "icon.ico"


render_bridge.install_tier_d_hook("tray_icon:product_module", _product_module)
render_bridge.install_tier_d_hook("tray_icon:default_icon_path", _default_icon_path)

(
    TrayIcon,
    get_tray_icon,
    cleanup_tray_icon,
    TRAY_ICON_GUID,
) = render_bridge.tier_d_reexports(
    "tray_icon",
    ("TrayIcon", "get_tray_icon", "cleanup_tray_icon", "TRAY_ICON_GUID"),
)

__all__ = [
    "TRAY_ICON_GUID",
    "TrayIcon",
    "cleanup_tray_icon",
    "get_tray_icon",
]
