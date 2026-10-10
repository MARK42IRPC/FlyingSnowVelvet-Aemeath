"""工作台设置页的共享布局原语（产品侧再导出垫片）。

`SettingsFormLayout` / `SettingsPageHeader` / `SettingsSection` / `SettingsActionBar` /
`SettingsPageScaffold` / `apply_settings_page_fonts` 是产品页面要继承/调用的 QWidget 骨架，
已下沉到档位 D 宿主 `lib/core/render/backends/qt/widgets/workbench_settings_layout.py`
（与 `workbench_page.py` 工具页基类同级）。

本模块不再是 Qt 实现、不再 `import PyQt5`：名字与字体入口都经 `render_bridge` 转发。
`SmoothScrollArea` 仍按原名字再导出（它本就住在档位 D 的 `backends/qt/widgets/smooth_scroll.py`，
这里只是把解析收到 bridge）。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    SettingsFormLayout,
    SettingsPageHeader,
    SettingsSection,
    SettingsActionBar,
    SettingsPageScaffold,
    apply_settings_page_fonts,
    SETTINGS_LABEL_WIDTH,
    SETTINGS_FONT_SIZE,
    SETTINGS_HINT_FONT_SIZE,
) = render_bridge.settings_layout_primitives()

SmoothScrollArea = render_bridge.smooth_scroll_classes()

#: 与下沉前逐字段同源；宿主因此不必反向 import 产品面。
render_bridge.configure_settings_layout_font(render_bridge.ui_font)

create_settings_form = SettingsFormLayout

__all__ = [
    "SETTINGS_FONT_SIZE",
    "SETTINGS_HINT_FONT_SIZE",
    "SETTINGS_LABEL_WIDTH",
    "SettingsActionBar",
    "SettingsFormLayout",
    "SettingsPageHeader",
    "SettingsPageScaffold",
    "SettingsSection",
    "SmoothScrollArea",
    "apply_settings_page_fonts",
    "create_settings_form",
]
