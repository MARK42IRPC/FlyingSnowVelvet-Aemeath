"""Workbench settings page schema（产品侧再导出垫片）。

`SettingsSectionSpec` / `SettingsPageSpec` / `SETTINGS_PAGE_SPECS` 与 `GENERAL_CONFIG_CATEGORIES`
已下沉到后端中立的 `lib/core/settings_schema/schema.py`（档位 D 的设置页宿主需要这份分区表）。
这里按原名再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.core.settings_schema.schema import (  # noqa: F401
    GENERAL_CONFIG_CATEGORIES,
    SETTINGS_PAGE_SPECS,
    SettingsPageSpec,
    SettingsSectionSpec,
)

__all__ = [
    "GENERAL_CONFIG_CATEGORIES",
    "SETTINGS_PAGE_SPECS",
    "SettingsPageSpec",
    "SettingsSectionSpec",
]
