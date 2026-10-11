"""AI 设置「AI 主页面」区块装配方法（产品侧再导出垫片）。

七个区块装配方法已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/ai_settings_page_sections.py`。这里按原名再导出
`AISettingsPageSectionsMixin`，既有继承面（`AISettingsPageMixin` / `AISettingsPanel`）不变。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(AISettingsPageSectionsMixin,) = render_bridge.tier_d_reexports(
    "ai_settings_page_sections",
    ("AISettingsPageSectionsMixin",),
)

__all__ = ["AISettingsPageSectionsMixin"]
