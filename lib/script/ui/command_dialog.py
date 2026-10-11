"""CMD 命令输入框（产品侧再导出垫片）。

`CommandDialog` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/command_dialog.py`。本模块按原名再导出，
`pet_window_ui` 与测试的既有导入面不变。

宿主不得 import `lib.script`：事件处理器由本垫片在导入时经 seam 钩子登记。
本模块不再 `import PyQt5`，冻结清单里不再有它。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def _event_handler_factory():
    from lib.script.ui.command_dialog_handler import CommandDialogEventHandler

    return CommandDialogEventHandler


render_bridge.install_tier_d_seams(
    draw_backend=render_bridge.create_draw_backend,
    color_name=render_bridge.qt_color_name,
    cmd_font=render_bridge.cmd_font,
)
render_bridge.install_tier_d_hook("command_dialog:event_handler", _event_handler_factory)

(CommandDialog,) = render_bridge.tier_d_reexports("command_dialog", ("CommandDialog",))

__all__ = ["CommandDialog"]
