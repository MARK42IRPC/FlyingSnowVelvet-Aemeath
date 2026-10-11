"""AI 设置面板的「配置取值 / 回填 / 恢复 / 保存」控制器（产品侧再导出垫片）。

`ConfigStoreMixin` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/ai_settings_config_store.py`。这里按原名再导出，既有继承面不变；
音乐 / GSV 服务、GPU 模式换算与 AI 存储入口由本垫片导入时注入。
"""

from __future__ import annotations

from lib.script.gsvmove import get_gsvmove_service
from lib.script.music import get_music_service
from lib.script.ui import render_bridge
from lib.script.ui.ai_settings_page import _gpu_mode_from_num_gpu
from lib.script.ui.ai_settings_storage import apply_ai_runtime, save_ai_values

(ConfigStoreMixin,) = render_bridge.tier_d_reexports(
    "ai_settings_config_store", ("ConfigStoreMixin",)
)


def _music_set_provider(provider: str) -> None:
    get_music_service().set_provider(str(provider), persist=False)


def _gsvmove_cleanup_audio_cache() -> None:
    get_gsvmove_service().cleanup_saved_audio_cache()


render_bridge.install_tier_d_hook("gpu_mode_from_num_gpu", _gpu_mode_from_num_gpu)
render_bridge.install_tier_d_hook("music_set_provider", _music_set_provider)
render_bridge.install_tier_d_hook("gsvmove_cleanup_audio_cache", _gsvmove_cleanup_audio_cache)
render_bridge.install_tier_d_hook("ai_storage_save", save_ai_values)
render_bridge.install_tier_d_hook("ai_storage_apply_runtime", apply_ai_runtime)

__all__ = ["ConfigStoreMixin"]
