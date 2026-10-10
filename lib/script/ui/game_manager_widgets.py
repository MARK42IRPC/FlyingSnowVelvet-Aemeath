"""游戏管理窗口的卡片控件（产品侧装配垫片）。

`_GameCardWidget` 是纯 Qt 控件，已下沉到档位 D 宿主
`lib/core/render/backends/qt/widgets/game_manager_widgets.py`。宿主不得 import `lib.script`
（render 层规则），所以它不认 `InstalledGame`；产品侧在这里补回原有调用签名。

本模块不再是 `QFrame` 实现、不再 `import PyQt5`：`_GameCardWidget` 的名字与字体入口都经
`render_bridge` 转发，冻结清单里不再有它，`game_manager_window.py` 的导入面与调用点零改动。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

_HostGameCardWidget = render_bridge.game_card_widget_class()

render_bridge.configure_game_card_font(render_bridge.ui_font)


def _GameCardWidget(record, parent=None):
    """宿主卡片的装配入口：保留 `(record, parent)` 的原有调用签名。"""
    return _HostGameCardWidget(record, parent)


__all__ = [
    "_GameCardWidget",
]
