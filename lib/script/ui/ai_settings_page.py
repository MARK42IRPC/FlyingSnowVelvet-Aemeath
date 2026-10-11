"""AI 设置面板的「AI 主页面」（产品侧再导出垫片）。

`AISettingsPageMixin`（含 `_build_ui` 与取值/回填闭环）与小助手 `_gpu_mode_from_num_gpu` /
`_num_gpu_from_mode` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/ai_settings_page.py`。这里按原名再导出，既有继承面与导入面不变；
chat 模型表 / 人格文件 / GSV 语音包、AI 存储入口与语音包安装控件由本垫片在导入时注入。

注意注入必须**先于**再导出：宿主在 import 期就用 `welfare_auto_companion_interval_ms` 注入物算出
固定间隔常量，晚注入会让它读到缺省值。
"""

from __future__ import annotations

from lib.script.chat.handler_auto_companion import WELFARE_AUTO_COMPANION_INTERVAL_MS
from lib.script.chat.ollama_registry import get_available_model_names, get_model_list_error
from lib.script.chat.persona_storage import ensure_user_persona_file
from lib.script.gsvmove import get_gsvmove_service, get_voice_package_status
from lib.script.ui import render_bridge
from lib.script.ui.ai_settings_storage import (
    apply_ai_runtime,
    load_ai_values,
    save_ai_values,
)
from lib.script.ui.voice_package_installer import (
    VoicePackageInstallBanner,
    VoicePackageInstallerDialog,
    VoicePackageManagementBar,
)


def _welfare_auto_companion_interval_ms():
    return WELFARE_AUTO_COMPANION_INTERVAL_MS


def _voice_package_widgets():
    return (VoicePackageInstallBanner, VoicePackageInstallerDialog, VoicePackageManagementBar)


render_bridge.install_tier_d_hook("voice_package_status", get_voice_package_status)
render_bridge.install_tier_d_hook("ollama_available_model_names", get_available_model_names)
render_bridge.install_tier_d_hook("ollama_model_list_error", get_model_list_error)
render_bridge.install_tier_d_hook("ensure_user_persona_file", ensure_user_persona_file)
render_bridge.install_tier_d_hook("gsvmove_service", get_gsvmove_service)
render_bridge.install_tier_d_hook("ai_storage_load", load_ai_values)
render_bridge.install_tier_d_hook("ai_storage_save", save_ai_values)
render_bridge.install_tier_d_hook("ai_storage_apply_runtime", apply_ai_runtime)
render_bridge.install_tier_d_hook("voice_package_widgets", _voice_package_widgets)
render_bridge.install_tier_d_hook(
    "welfare_auto_companion_interval_ms", _welfare_auto_companion_interval_ms
)

(
    AISettingsPageMixin,
    _gpu_mode_from_num_gpu,
    _num_gpu_from_mode,
) = render_bridge.tier_d_reexports("ai_settings_page", (
    "AISettingsPageMixin",
    "_gpu_mode_from_num_gpu",
    "_num_gpu_from_mode",
))

__all__ = [
    "AISettingsPageMixin",
    "_gpu_mode_from_num_gpu",
    "_num_gpu_from_mode",
]
