"""AI 设置面板「配置分类页」（产品侧再导出垫片）。

`ConfigPageMixin` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/ai_settings_config_page.py`。这里按原名再导出，既有继承面不变；
动画目录解析 / 开机启动读写经 `tier_d_seams` 由本垫片注入。
"""

from __future__ import annotations

from lib.script.SEanima.clip import resolve_animation_folder_path
from lib.script.SEanima.decoder import scan_animation_frame_files
from lib.script.app.autostart import is_autostart_enabled
from lib.script.app.tray_actions import set_autostart_enabled
from lib.script.ui import render_bridge

(ConfigPageMixin,) = render_bridge.tier_d_reexports(
    "ai_settings_config_page", ("ConfigPageMixin",)
)

#: 公告偏好的产品侧入口：宿主按 `tier_d_seams.hook` 惰性取用，因此对本模块同名函数的 patch
#: （`patch.object(ai_settings_config_page, "load_announcement_preferences", ...)`）照旧生效。
from lib.script.ui.announcement_dialog import (  # noqa: E402 - 需在 reexports 之后注入
    load_announcement_preferences,
    set_announcement_forever_suppressed,
)


def _scan_animation_frame_files(folder_path: str) -> list[str]:
    return list(scan_animation_frame_files(str(folder_path)))


def _autostart_is_enabled() -> bool:
    return bool(is_autostart_enabled())


def _autostart_set_enabled(enabled: bool):
    return set_autostart_enabled(bool(enabled))


render_bridge.install_tier_d_anim_folders(resolve_path=resolve_animation_folder_path)
render_bridge.install_tier_d_hook("scan_animation_frame_files", _scan_animation_frame_files)
render_bridge.install_tier_d_hook("autostart_is_enabled", _autostart_is_enabled)
render_bridge.install_tier_d_hook("autostart_set_enabled", _autostart_set_enabled)
render_bridge.install_tier_d_hook(
    "announcement_load_preferences",
    lambda *a, **k: load_announcement_preferences(*a, **k),
)
render_bridge.install_tier_d_hook(
    "announcement_set_forever_suppressed",
    lambda *a, **k: set_announcement_forever_suppressed(*a, **k),
)

__all__ = ["ConfigPageMixin"]
