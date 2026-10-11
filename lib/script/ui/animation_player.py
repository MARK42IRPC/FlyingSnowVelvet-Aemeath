"""独立动画播放器（产品侧垫片）。

`AnimationWindow` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/animation_player.py`。本模块按原名再导出，既有导入面
不变；SEanima 的四个解码入口（`resolve_animation_clip` / `build_playback_plan` /
`select_playback_files` / `decode_frame_to_bytes`）由本垫片在导入时登记进 seam 钩子表。

同时保留 `SEanima.effects` 阴影换算函数的再导出
（`tests/test_exit_animation_shadow_config.py` 从这里取），既有导入面不变。

子进程入口见 `lib/core/render/backends/qt/widgets/animation_player_main.py`
（`SEanima/animation.py` 启动的就是它）。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def _resolve_clip(animation_type: str):
    from lib.script.SEanima.clip import resolve_animation_clip

    return resolve_animation_clip(animation_type)


def _build_playback_plan(clip):
    from lib.script.SEanima.decoder import build_playback_plan

    return build_playback_plan(clip)


def _select_playback_files(files, **kwargs):
    from lib.script.SEanima.decoder import select_playback_files

    return select_playback_files(files, **kwargs)


def _decode_frame_to_bytes(path, plan):
    from lib.script.SEanima.decoder import decode_frame_to_bytes

    return decode_frame_to_bytes(path, plan)


render_bridge.install_tier_d_hook("animation_player:resolve_clip", _resolve_clip)
render_bridge.install_tier_d_hook("animation_player:build_playback_plan", _build_playback_plan)
render_bridge.install_tier_d_hook("animation_player:select_playback_files", _select_playback_files)
render_bridge.install_tier_d_hook("animation_player:decode_frame_to_bytes", _decode_frame_to_bytes)

(AnimationWindow,) = render_bridge.tier_d_reexports("animation_player", ("AnimationWindow",))

#: 退出动画阴影的换算函数定义在 SEanima.effects；测试从这里取，保持再导出。
from lib.script.SEanima.effects import _build_exit_shadow_metrics, _normalize_exit_shadow_direction  # noqa: E402

__all__ = [
    "AnimationWindow",
    "_build_exit_shadow_metrics",
    "_normalize_exit_shadow_direction",
]
