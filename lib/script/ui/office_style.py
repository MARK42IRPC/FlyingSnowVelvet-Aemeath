"""办公面样式（产品面门面）：数据在渲染层，控件树辅助在后端宿主。

整份 QSS、字号档与档位配色是后端中立事实，已下沉到
`lib/core/render/visuals/office_chrome.py`；`apply_office_fonts()` /
`create_office_accent_bar()` 必须操作真实 `QWidget`，落在
`lib/core/render/backends/qt/widgets/office_widgets.py`。本模块只保留历史导入名，
让办公页、审批弹窗与既有测试继续按原路径取用；新代码请直接引用落点。

`office_effort_colors()` / `office_stylesheet()` 的档位数量参数带默认值：档位数是办公产品
的契约事实（`lib/script/office/contracts.REASONING_EFFORTS`），而 `lib/core/render` 不得
反向 import `lib.script`，所以由这一层的门面把权威值喂给中立实现。
"""

from __future__ import annotations

from lib.core.render.visuals.office_chrome import (
    OFFICE_BOLD_LABELS,
    OFFICE_BUBBLE_PAD_H,
    OFFICE_BUBBLE_PAD_V,
    OFFICE_HINT_LABELS,
    SETTINGS_FONT_SIZE,
    SETTINGS_HINT_FONT_SIZE,
)
from lib.core.render.visuals import office_chrome as _chrome
from lib.script.office.contracts import REASONING_EFFORTS
from lib.script.ui import render_bridge
from lib.script.ui.workbench_settings_layout import (
    SETTINGS_FONT_SIZE as _SETTINGS_FONT_SIZE,
)
from lib.script.ui.workbench_settings_layout import (
    SETTINGS_HINT_FONT_SIZE as _SETTINGS_HINT_FONT_SIZE,
)


def create_office_accent_bar(parent):
    """办公面顶部的双色 accent bar（青 / 粉各半，真实控件由 Qt 宿主构造）。"""
    return render_bridge.create_office_accent_bar(parent)


def apply_office_fonts(root) -> None:
    """把工作台设置页的字号档铺到办公面的整棵控件树。

    先套用设置页的现成口径（页头说明、分区标题 / 说明、输入控件），再补办公面独有的
    工具按钮、文本视图、标签页与 `Office*` 标签；字号取产品面的权威值，两个页面因此
    共用同一套字号。
    """
    render_bridge.apply_settings_page_fonts(root)
    render_bridge.apply_office_widget_fonts(
        root,
        settings_font_size=_SETTINGS_FONT_SIZE,
        settings_hint_font_size=_SETTINGS_HINT_FONT_SIZE,
    )


def office_effort_colors(mode: str | None = None) -> tuple[str, ...]:
    """推理强度各档的档位色：按强度从淡粉线性过渡到青。"""
    return _chrome.office_effort_colors(mode, effort_steps=len(REASONING_EFFORTS))


def office_stylesheet(
    mode: str | None = None,
    *,
    page_name: str = "OfficeWorkbenchPage",
    standalone: bool = False,
) -> str:
    """办公页样式表；`standalone=True` 用于自带画布的独立窗口。"""
    return _chrome.office_stylesheet(
        mode,
        page_name=page_name,
        standalone=standalone,
        effort_steps=len(REASONING_EFFORTS),
    )


__all__ = [
    "OFFICE_BOLD_LABELS",
    "OFFICE_BUBBLE_PAD_H",
    "OFFICE_BUBBLE_PAD_V",
    "OFFICE_HINT_LABELS",
    "SETTINGS_FONT_SIZE",
    "SETTINGS_HINT_FONT_SIZE",
    "apply_office_fonts",
    "create_office_accent_bar",
    "office_effort_colors",
    "office_stylesheet",
]
