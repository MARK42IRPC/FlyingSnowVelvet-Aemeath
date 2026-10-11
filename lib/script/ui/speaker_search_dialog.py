"""音响搜索 UI（产品侧再导出垫片）。

`SpeakerSearchDialog` 与它的全局单例已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/speaker_search_dialog.py`。本模块按原名再导出，
`speaker_control_buttons` / `preloader` / `shutdown` / 测试的既有导入面不变。

宿主不得 import `lib.script`：音响菜单样式门面、音乐服务、搜索结果框、控制按钮组、播放队列
面板与点击粒子一律由本垫片在导入时经 seam 钩子登记；本模块不再 `import PyQt5`，冻结清单里
不再有它。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

#: 全局单例：宿主构造时经 seam 钩子回填，`get_speaker_search_dialog()` 直接读这里。
_instance: "SpeakerSearchDialog | None" = None


def _menu_style():
    from lib.script.ui import speaker_menu_style

    return speaker_menu_style


def _music_service():
    from lib.script.music import get_music_service

    return get_music_service()


def _first_artist(items):
    from lib.script.music.track_text import first_artist_from_list

    return first_artist_from_list(items)


def _format_duration(value):
    from lib.script.music.track_text import format_duration_text

    return format_duration_text(value)


def _result_box_class():
    from lib.script.ui.speaker_search_result_box import SpeakerSearchResultBox

    return SpeakerSearchResultBox


def _control_buttons_class():
    from lib.script.ui.speaker_control_buttons import SpeakerControlButtons

    return SpeakerControlButtons


def _playlist_panel_getter():
    from lib.script.ui.playlist_panel import get_playlist_panel

    return get_playlist_panel


def _click_particle(widget, event):
    from lib.script.ui._particle_helper import publish_click_particle

    return publish_click_particle(widget, event)


def _register_instance(instance):
    global _instance
    _instance = instance


def _unregister_instance():
    global _instance
    _instance = None


# 宿主只用档位 1 的 `resolve_placement()` 解算落位（与 `speaker_search_result_box` 同源），
# 这里在垫片里保留一次引用，供 `tests/test_render_layout_algorithms.py` 的迁移守卫检查。
_placement_seam = render_bridge.resolve_placement
render_bridge.install_tier_d_seams(draw_backend=render_bridge.create_draw_backend)
render_bridge.install_tier_d_hook("click_particle", _click_particle)
render_bridge.install_tier_d_presentation_host(render_bridge.presentation_host)
render_bridge.install_tier_d_hook("speaker_search_dialog:menu_style", _menu_style)
render_bridge.install_tier_d_hook("speaker_search_dialog:music_service", _music_service)
render_bridge.install_tier_d_hook("speaker_search_dialog:first_artist", _first_artist)
render_bridge.install_tier_d_hook("speaker_search_dialog:format_duration", _format_duration)
render_bridge.install_tier_d_hook("speaker_search_dialog:result_box_class", _result_box_class)
render_bridge.install_tier_d_hook("speaker_search_dialog:control_buttons_class", _control_buttons_class)
render_bridge.install_tier_d_hook("speaker_search_dialog:playlist_panel_getter", _playlist_panel_getter)
render_bridge.install_tier_d_hook("speaker_search_dialog:register_instance", _register_instance)
render_bridge.install_tier_d_hook("speaker_search_dialog:unregister_instance", _unregister_instance)

(
    SpeakerSearchDialog,
    _INPUT_W,
    _BTN_W,
    _HEIGHT,
) = render_bridge.tier_d_reexports(
    "speaker_search_dialog",
    ("SpeakerSearchDialog", "_INPUT_W", "_BTN_W", "_HEIGHT"),
)


def get_speaker_search_dialog() -> "SpeakerSearchDialog | None":
    """获取全局搜索 UI 单例（未初始化时返回 None）。"""
    return _instance


def init_speaker_search_dialog() -> "SpeakerSearchDialog":
    """初始化并返回全局搜索 UI 单例，需在 Qt 主线程中调用。"""
    global _instance
    if _instance is None:
        _instance = SpeakerSearchDialog()
    return _instance


def cleanup_speaker_search_dialog():
    """释放全局搜索 UI 资源（程序退出时调用）。"""
    global _instance
    if _instance is not None:
        try:
            _instance.close()
        except Exception:
            pass
        _instance = None


__all__ = [
    "SpeakerSearchDialog",
    "get_speaker_search_dialog",
    "init_speaker_search_dialog",
    "cleanup_speaker_search_dialog",
]
