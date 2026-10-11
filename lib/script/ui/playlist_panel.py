"""播放列表栏（产品侧再导出垫片）。

`PlaylistPanel` 与它的全局单例已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/playlist_panel.py`。本模块按原名再导出，
`progress_panel` / `speaker_control_buttons` / `preloader` / `shutdown` / 测试的既有导入面
不变；尺寸常量与两个自绘小按钮仍从 `playlist_queue_buttons` 转发（既有导入面不变）。

宿主不得 import `lib.script`：音乐服务、控制按钮族、进度条面板、翻页按钮助手与说明文本
一律由本垫片在导入时经 seam 钩子登记；本模块不再 `import PyQt5`，冻结清单里不再有它。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

#: 全局单例：宿主构造时经 seam 钩子回填，`get_playlist_panel()` 直接读这里。
_instance: "PlaylistPanel | None" = None

#: 队列面板的尺寸常量与两个自绘小按钮：按原名再导出，既有导入面不变。
from lib.script.ui.playlist_queue_buttons import (  # noqa: E402,F401 - 既有导入面
    _AUTO_HIDE_MOUSE_DISTANCE,
    _BORDER,
    _GAP,
    _LAYER,
    _PAD_X,
    _PAGE_SIZE,
    _QueuePlayNowButton,
    _QueueRemoveButton,
    _REMOVE_BTN_H,
    _REMOVE_BTN_W,
    _ROW_H,
    _WIDTH,
)


def _music_service():
    from lib.script.music import get_music_service

    return get_music_service()


def _control_buttons_module():
    from lib.script.ui import speaker_control_buttons

    return speaker_control_buttons


def _page_buttons_module():
    from lib.script.ui import page_turn_buttons

    return page_turn_buttons


def _progress_panel_getter():
    from lib.script.ui.progress_panel import get_progress_panel

    return get_progress_panel


def _tooltips():
    from config.tooltip_config import TOOLTIPS

    return TOOLTIPS


def _click_particle(widget, event):
    from lib.script.ui._particle_helper import publish_click_particle

    return publish_click_particle(widget, event)


def _register_instance(instance):
    global _instance
    _instance = instance


def _unregister_instance():
    global _instance
    _instance = None


# 宿主只用档位 1 的 `resolve_placement()` 解算落位；这里在垫片里保留一次引用，
# 供 `tests/test_render_layout_algorithms.py` 的迁移守卫检查。
_placement_seam = render_bridge.resolve_placement
render_bridge.install_tier_d_seams(
    draw_backend=render_bridge.create_draw_backend,
    text_metrics=render_bridge.text_metrics,
)
render_bridge.install_tier_d_hook("click_particle", _click_particle)
render_bridge.install_tier_d_presentation_host(render_bridge.presentation_host)
render_bridge.install_tier_d_hook("playlist_panel:music_service", _music_service)
render_bridge.install_tier_d_hook("playlist_panel:control_buttons_module", _control_buttons_module)
render_bridge.install_tier_d_hook("playlist_panel:page_buttons_module", _page_buttons_module)
render_bridge.install_tier_d_hook("playlist_panel:progress_panel_getter", _progress_panel_getter)
render_bridge.install_tier_d_hook("playlist_panel:tooltips", _tooltips)
render_bridge.install_tier_d_hook("playlist_panel:register_instance", _register_instance)
render_bridge.install_tier_d_hook("playlist_panel:unregister_instance", _unregister_instance)

(PlaylistPanel,) = render_bridge.tier_d_reexports("playlist_panel", ("PlaylistPanel",))


def get_playlist_panel() -> "PlaylistPanel | None":
    """获取全局播放列表栏单例（未初始化时返回 None）。"""
    return _instance


def init_playlist_panel() -> "PlaylistPanel":
    """初始化并返回全局播放列表栏单例，需在 Qt 主线程中调用。"""
    global _instance
    if _instance is None:
        _instance = PlaylistPanel()
    return _instance


def cleanup_playlist_panel():
    """释放全局播放列表栏资源（程序退出时调用）。"""
    global _instance
    if _instance is not None:
        try:
            _instance.close()
        except Exception:
            pass
        _instance = None


__all__ = [
    "PlaylistPanel",
    "get_playlist_panel",
    "init_playlist_panel",
    "cleanup_playlist_panel",
]
