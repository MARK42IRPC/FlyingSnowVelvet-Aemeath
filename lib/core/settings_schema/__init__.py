"""工作台设置页 schema 的后端中立事实源。

`SettingsSectionSpec` / `SettingsPageSpec` / `GENERAL_CONFIG_CATEGORIES` 原本住在
`lib/script/workbench/settings/schema.py`；档位 D 的设置页宿主需要这份分区表，而宿主不得 import
产品包，因此这份纯数据类型下沉到 core。`lib/script/workbench/settings/__init__.py` 与
`lib/script/ui/ai_settings_*` 仍按原名再导出，调用方与既有测试导入面不变。
"""

from lib.core.settings_schema.schema import (
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
