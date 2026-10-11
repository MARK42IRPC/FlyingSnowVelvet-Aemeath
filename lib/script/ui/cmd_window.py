"""CMD 窗口（产品侧再导出垫片）。

`CmdWindow` 与它的全局单例已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/cmd_window.py`。本模块按原名再导出，
`qt_application_ui` / `shutdown` / 测试的既有导入面不变。

历史上它还再导出 `cmd_window_widgets` 的边缘助手与标题按钮；为保留那一导入面，
本垫片照旧转发这批名字。本模块不再 `import PyQt5`，冻结清单里不再有它。
"""

import os  # noqa: F401 - 既有导入面
import threading  # noqa: F401 - 既有导入面
import subprocess  # noqa: F401 - 既有导入面
from collections import deque  # noqa: F401 - 既有导入面

from lib.script.ui import render_bridge

from lib.script.ui.cmd_window_widgets import (  # noqa: F401 - 既有导入面
    _CloseButton,
    _EDGE_CURSORS,
    _StreamDoneEvent,
    _StreamLineEvent,
    _TitleButton,
    _hex,
    _hit_edge,
    _strip_ansi,
)

render_bridge.install_tier_d_seams(
    ui_font=render_bridge.ui_font,
    digit_font=render_bridge.digit_font,
    cmd_font=render_bridge.cmd_font,
    draw_backend=render_bridge.create_draw_backend,
    color=render_bridge.qt_color,
)

(
    CmdWindow,
    get_cmd_window,
    cleanup_cmd_window,
) = render_bridge.tier_d_reexports(
    "cmd_window", ("CmdWindow", "get_cmd_window", "cleanup_cmd_window")
)

__all__ = [
    "CmdWindow",
    "cleanup_cmd_window",
    "get_cmd_window",
]
