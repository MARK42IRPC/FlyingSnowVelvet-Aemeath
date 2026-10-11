"""工作台风格的「技能管理 / 插件管理」卡片（产品侧再导出垫片）。

`OfficeManagerCard` 与 `VISIBLE_ROWS` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/office_manager_card.py`。这里按原名再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(OfficeManagerCard, VISIBLE_ROWS) = render_bridge.tier_d_reexports(
    "office_manager_card", ("OfficeManagerCard", "VISIBLE_ROWS")
)

__all__ = ["OfficeManagerCard", "VISIBLE_ROWS"]
