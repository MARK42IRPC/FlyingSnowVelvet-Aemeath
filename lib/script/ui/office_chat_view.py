"""办公任务详情的 Markdown 气泡视图（产品侧再导出垫片）。

`OfficeConversationView` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/office_chat_view.py`。这里按原名再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(OfficeConversationView,) = render_bridge.tier_d_reexports(
    "office_chat_view", ("OfficeConversationView",)
)

__all__ = ["OfficeConversationView"]
