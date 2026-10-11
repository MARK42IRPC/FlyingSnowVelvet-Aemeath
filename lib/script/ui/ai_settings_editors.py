"""AI 设置面板「配置编辑器控件族」（产品侧再导出垫片）。

编辑器控件族已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/ai_settings_editors.py`。这里按原名再导出，既有导入面不变；
动画目录枚举 / 路径解析 / 帧扫描 / 时长换算 / 目录显示名与数字字体入口由本垫片导入时注入。
"""

from __future__ import annotations

from lib.script.SEanima.clip import (
    list_animation_folder_choices,
    resolve_animation_folder_path,
)
from lib.script.SEanima.decoder import playback_duration_seconds, scan_animation_frame_files
from lib.script.ui import render_bridge
from lib.script.ui.ai_settings_labels import animation_folder_display_name

(
    ConfigEditorMixin,
    _AnimationDurationSliderField,
    _DecimalSliderField,
    _NoWheelSlider,
) = render_bridge.tier_d_reexports("ai_settings_editors", (
    "ConfigEditorMixin",
    "_AnimationDurationSliderField",
    "_DecimalSliderField",
    "_NoWheelSlider",
))

render_bridge.install_tier_d_anim_folders(
    choices=list_animation_folder_choices,
    resolve_path=resolve_animation_folder_path,
)
render_bridge.install_tier_d_hook("scan_animation_frame_files", scan_animation_frame_files)
render_bridge.install_tier_d_hook("playback_duration_seconds", playback_duration_seconds)
render_bridge.install_tier_d_hook("animation_folder_display_name", animation_folder_display_name)

render_bridge.install_tier_d_seams(
    digit_font=render_bridge.digit_font,
    draw_backend=render_bridge.create_draw_backend,
    color=render_bridge.qt_color,
    color_name=render_bridge.qt_color_name,
)

__all__ = [
    "ConfigEditorMixin",
    "_AnimationDurationSliderField",
    "_DecimalSliderField",
    "_NoWheelSlider",
]
