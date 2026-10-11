"""Staged startup preloading and pre-rendering for lazily-created UI windows（产品侧垫片）。

`UiPreloader` 与两个入口已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/preloader.py`。本模块按原名再导出，既有导入面
（`qt_application_ui` / `tests/test_ui_preloader.py`）不变；5 个预加载步骤的构造 / 释放
入口是产品事实，由本垫片在导入时登记进 `tier_d_seams` 的钩子表，宿主按稳定名字取用。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

# 每个预加载步骤的产品侧构造 / 释放入口。宿主（档位 D）不得 import `lib.script`，
# 因此这些「要调一个产品包函数」的连线都在这里登记，键名与宿主 `_PRELOAD_STEPS` 一致。


def _init_playlist_panel():
    from lib.script.ui.playlist_panel import init_playlist_panel

    return init_playlist_panel()


def _cleanup_playlist_panel() -> None:
    from lib.script.ui.playlist_panel import cleanup_playlist_panel

    cleanup_playlist_panel()


def _init_progress_panel():
    from lib.script.ui.progress_panel import init_progress_panel

    return init_progress_panel()


def _cleanup_progress_panel() -> None:
    from lib.script.ui.progress_panel import cleanup_progress_panel

    cleanup_progress_panel()


def _init_speaker_search_dialog():
    from lib.script.ui.speaker_search_dialog import init_speaker_search_dialog

    return init_speaker_search_dialog()


def _cleanup_speaker_search_dialog() -> None:
    from lib.script.ui.speaker_search_dialog import cleanup_speaker_search_dialog

    cleanup_speaker_search_dialog()


def _init_cloudmusic_login_dialog():
    from lib.script.ui.cloudmusic_login_dialog import init_cloudmusic_login_dialog

    return init_cloudmusic_login_dialog()


def _cleanup_cloudmusic_login_dialog() -> None:
    from lib.script.ui.cloudmusic_login_dialog import cleanup_cloudmusic_login_dialog

    cleanup_cloudmusic_login_dialog()


def _init_yuanbao_login_dialog():
    from lib.script.ui.yuanbao_login_dialog import init_yuanbao_login_dialog

    return init_yuanbao_login_dialog()


def _cleanup_yuanbao_login_dialog() -> None:
    from lib.script.ui.yuanbao_login_dialog import cleanup_yuanbao_login_dialog

    cleanup_yuanbao_login_dialog()


for _name, _factory in (
    ('preloader:init_playlist_panel', _init_playlist_panel),
    ('preloader:cleanup_playlist_panel', _cleanup_playlist_panel),
    ('preloader:init_progress_panel', _init_progress_panel),
    ('preloader:cleanup_progress_panel', _cleanup_progress_panel),
    ('preloader:init_speaker_search_dialog', _init_speaker_search_dialog),
    ('preloader:cleanup_speaker_search_dialog', _cleanup_speaker_search_dialog),
    ('preloader:init_cloudmusic_login_dialog', _init_cloudmusic_login_dialog),
    ('preloader:cleanup_cloudmusic_login_dialog', _cleanup_cloudmusic_login_dialog),
    ('preloader:init_yuanbao_login_dialog', _init_yuanbao_login_dialog),
    ('preloader:cleanup_yuanbao_login_dialog', _cleanup_yuanbao_login_dialog),
):
    render_bridge.install_tier_d_hook(_name, _factory)


(
    UiPreloader,
    estimate_widget_bytes,
    preload_runtime_ui,
    prewarm_runtime_ui_cache,
    ui_cache_preload_enabled,
    WIDGET_FIXED_OVERHEAD_BYTES,
    PRECACHE_IMAGE_LIMIT,
) = render_bridge.tier_d_reexports(
    "preloader",
    (
        "UiPreloader",
        "estimate_widget_bytes",
        "preload_runtime_ui",
        "prewarm_runtime_ui_cache",
        "ui_cache_preload_enabled",
        "WIDGET_FIXED_OVERHEAD_BYTES",
        "PRECACHE_IMAGE_LIMIT",
    ),
)

__all__ = [
    "UiPreloader",
    "estimate_widget_bytes",
    "preload_runtime_ui",
    "prewarm_runtime_ui_cache",
    "ui_cache_preload_enabled",
    "WIDGET_FIXED_OVERHEAD_BYTES",
    "PRECACHE_IMAGE_LIMIT",
]
