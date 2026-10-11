"""AI 设置面板：编辑并保存 config/ollama_config.py（产品侧再导出垫片）。

`AISettingsPanel` 与五个 mixin 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/ai_settings_panel.py`（总装宿主）与同族 `ai_settings_*`
宿主。本模块按原名再导出，既有导入面（`workbench_helper_entry` / `tray_icon` / 测试）不变。

需要读产品状态的两处入口——语音包状态与「桌宠更新」页模块——由本垫片在导入时经 seam 钩子
登记：宿主不得 import `lib.script`，因此这些连线只能在这里做。
"""

from __future__ import annotations

from lib.script.ui import ai_settings_about  # noqa: F401 - 导入即注入
from lib.script.ui import ai_settings_config_page  # noqa: F401 - 导入即注入
from lib.script.ui import ai_settings_config_store  # noqa: F401 - 导入即注入
from lib.script.ui import ai_settings_editors  # noqa: F401 - 导入即注入
from lib.script.ui import ai_settings_page  # noqa: F401 - 导入即注入
from lib.script.ui import ai_settings_shell  # noqa: F401 - 导入即注入
from lib.script.ui import ai_settings_tabs  # noqa: F401 - 导入即注入
from lib.script.ui import ai_settings_update  # noqa: F401 - 导入即注入
from lib.script.ui import render_bridge


def _voice_package_status():
    from lib.script.gsvmove import get_voice_package_status

    return get_voice_package_status()


def _ai_settings_update_module():
    from lib.script.ui import ai_settings_update as _update_page

    return _update_page


render_bridge.install_tier_d_hook("voice_package_status", _voice_package_status)
render_bridge.install_tier_d_hook("ai_settings_update_module", _ai_settings_update_module)

(
    AISettingsPanel,
    _ContributionCardButton,
    _AnimationDurationSliderField,
    _GENERAL_CONFIG_CATEGORIES,
    _GENERAL_DECIMAL_SLIDER_SPECS,
    _EXTERNAL_CONFIG_FIELD_KINDS,
) = render_bridge.tier_d_reexports("ai_settings_panel", (
    "AISettingsPanel",
    "_ContributionCardButton",
    "_AnimationDurationSliderField",
    "_GENERAL_CONFIG_CATEGORIES",
    "_GENERAL_DECIMAL_SLIDER_SPECS",
    "_EXTERNAL_CONFIG_FIELD_KINDS",
))


def _project_root():
    """仓库根目录：从本垫片所在包上溯到仓库根。"""
    from pathlib import Path

    return Path(__file__).resolve().parents[3]


def _contribution_list_path():
    from lib.core.ai_settings.contributions import contribution_list_path

    return contribution_list_path(_project_root())


def _sponsor_author_image_path():
    from lib.core.ai_settings.contributions import sponsor_author_image_path

    return sponsor_author_image_path(_project_root())


def _load_contribution_records():
    from lib.core.ai_settings.contributions import load_contribution_records

    return load_contribution_records(_project_root())


__all__ = [
    "AISettingsPanel",
    "_ContributionCardButton",
    "_AnimationDurationSliderField",
    "_GENERAL_CONFIG_CATEGORIES",
    "_EXTERNAL_CONFIG_FIELD_KINDS",
    "_project_root",
    "_contribution_list_path",
    "_sponsor_author_image_path",
    "_load_contribution_records",
]
