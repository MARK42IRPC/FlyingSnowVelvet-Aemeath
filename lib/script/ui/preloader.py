"""Staged startup preloading and pre-rendering for lazily-created UI windows.

启动等待（`APP_PRE_START` → `APP_INIT_READY` 的 3 秒）期间，若「系统调度 → 启动」里的
“启动期预绘制缓存”（`STARTUP.ui_cache_preload`）已打开，本模块会先在事件循环的空转间隙里
逐个构造常用控件、对每个控件预绘制一次（让共享视觉层、绘制后端和字形缓存提前就绪），
再登记进 30 MiB 上限的 UI 缓存池。整个过程异步、可中断：没画完也照常启动，缓存满了按
最近最少使用淘汰旧控件，正在显示的控件不会被淘汰。

即使开关关闭，`preload_runtime_ui()` 仍按原来的分步预加载运行，只是不再抢在启动等待里做。
两端入口分别是 `prewarm_runtime_ui_cache()`（启动等待期抢跑，开关关闭时返回 None）与
`preload_runtime_ui()`（初始化就绪后的原分步预加载）。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtWidgets import QApplication

from lib.core.logger import get_logger
from lib.core.ui_cache import DEFAULT_BUDGET_BYTES, UiCachePool, UiCacheStats

_logger = get_logger(__name__)

#: 预绘制时给每个控件预留的固定开销（字体缓存、样式、子控件等），仅用于缓存池记账。
WIDGET_FIXED_OVERHEAD_BYTES = 256 * 1024
#: 预绘制离屏位图的边长上限，避免个别大窗口在启动期多占几 MB。
PRECACHE_IMAGE_LIMIT = 1024


def ui_cache_preload_enabled() -> bool:
    """启动期预绘制缓存是否已在“系统调度 → 启动”中开启。"""
    try:
        from config.config import STARTUP

        return bool(STARTUP.get('ui_cache_preload', False))
    except Exception:
        return False


def estimate_widget_bytes(widget) -> int:
    """估算一个控件常驻内存的字节数（仅用于缓存池的 LRU 记账）。"""
    try:
        width = max(1, int(widget.width()))
        height = max(1, int(widget.height()))
    except Exception:
        width = height = 1
    return width * height * 4 + WIDGET_FIXED_OVERHEAD_BYTES


class UiPreloader(QObject):
    """分步构造、预绘制并把控件登记进 UI 缓存池。"""

    def __init__(
        self,
        parent=None,
        *,
        pool: UiCachePool | None = None,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
    ) -> None:
        super().__init__(parent or QApplication.instance())
        self._started = False
        # 注意：缓存池实现了 __len__，空池是假值，这里必须显式判 None。
        self._pool = (
            pool
            if pool is not None
            else UiCachePool(budget_bytes, on_evict=self._on_cache_evict)
        )
        self._releases: dict[str, Callable[[], None]] = {}
        self._steps: tuple[
            tuple[str, Callable[[], object | None], Callable[[], None]], ...
        ] = (
            ('playlist_panel', self._preload_playlist_panel, self._release_playlist_panel),
            ('progress_panel', self._preload_progress_panel, self._release_progress_panel),
            (
                'speaker_search_dialog',
                self._preload_speaker_search_dialog,
                self._release_speaker_search_dialog,
            ),
            (
                'cloudmusic_login_dialog',
                self._preload_cloudmusic_login_dialog,
                self._release_cloudmusic_login_dialog,
            ),
            (
                'yuanbao_login_dialog',
                self._preload_yuanbao_login_dialog,
                self._release_yuanbao_login_dialog,
            ),
        )
        self._step_index = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._run_next)

    # ── 公开接口 ─────────────────────────────────────────────────────

    @property
    def pool(self) -> UiCachePool:
        return self._pool

    @property
    def stats(self) -> UiCacheStats:
        return self._pool.stats

    @property
    def finished(self) -> bool:
        return self._step_index >= len(self._steps)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._timer.start(0)

    def stop(self) -> None:
        self._timer.stop()

    def release_all(self) -> None:
        """淘汰全部隐藏的缓存条目；正在显示的控件留给自己关闭，避免打断用户操作。"""
        for key in self._pool.keys:
            payload = self._pool.get(key)
            if payload is None or self._is_protected(key, payload):
                continue
            self._pool.discard(key)

    # ── 分步执行 ─────────────────────────────────────────────────────

    def _run_next(self) -> None:
        if self._step_index >= len(self._steps):
            return

        name, preload, release = self._steps[self._step_index]
        self._step_index += 1
        started_at = time.perf_counter()
        try:
            widget = preload()
            if widget is None:
                _logger.debug('[ui.preload] %s unavailable', name)
            else:
                self._warm_paint(widget)
                size_bytes = estimate_widget_bytes(widget)
                self._releases[name] = release
                stored = self._pool.store(
                    name,
                    widget,
                    size_bytes,
                    protect=self._is_protected,
                )
                elapsed_ms = (time.perf_counter() - started_at) * 1000.0
                if stored:
                    _logger.debug(
                        '[ui.preload] %s ready in %.1f ms (%d KiB, cache %d/%d KiB)',
                        name,
                        elapsed_ms,
                        size_bytes // 1024,
                        self._pool.bytes // 1024,
                        self._pool.budget_bytes // 1024,
                    )
                else:
                    # 池子装不下就立刻释放，避免出现预算之外的常驻控件。
                    self._releases.pop(name, None)
                    _logger.info('[ui.preload] UI cache full, released %s', name)
                    try:
                        release()
                    except Exception:
                        _logger.exception('[ui.preload] failed to release %s', name)
        except Exception:
            _logger.exception('[ui.preload] failed: %s', name)

        if self._step_index < len(self._steps):
            self._timer.start(0)

    def _is_protected(self, _key: str, payload: object) -> bool:
        """正在显示的控件不能淘汰，否则会打断用户当前操作。"""
        try:
            return bool(payload.isVisible())
        except Exception:
            return True

    def _on_cache_evict(self, key: str, _payload: object) -> None:
        release = self._releases.pop(key, None)
        if release is None:
            return
        try:
            release()
            _logger.debug('[ui.preload] evicted cached UI: %s', key)
        except Exception:
            _logger.exception('[ui.preload] failed to release evicted UI: %s', key)

    @staticmethod
    def _warm_paint(widget) -> None:
        """离屏渲染一次，让共享视觉层、绘制后端和字形缓存提前就绪。"""
        try:
            width = min(PRECACHE_IMAGE_LIMIT, max(1, int(widget.width())))
            height = min(PRECACHE_IMAGE_LIMIT, max(1, int(widget.height())))
            image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            try:
                widget.render(painter)
            finally:
                painter.end()
        except Exception:
            _logger.debug('[ui.preload] warm paint skipped', exc_info=True)

    # ── 各控件的构造 / 释放 ──────────────────────────────────────────

    @staticmethod
    def _preload_playlist_panel():
        from lib.script.ui.playlist_panel import init_playlist_panel

        return init_playlist_panel()

    @staticmethod
    def _release_playlist_panel() -> None:
        from lib.script.ui.playlist_panel import cleanup_playlist_panel

        cleanup_playlist_panel()

    @staticmethod
    def _preload_progress_panel():
        from lib.script.ui.progress_panel import init_progress_panel

        return init_progress_panel()

    @staticmethod
    def _release_progress_panel() -> None:
        from lib.script.ui.progress_panel import cleanup_progress_panel

        cleanup_progress_panel()

    @staticmethod
    def _preload_speaker_search_dialog():
        from lib.script.ui.speaker_search_dialog import init_speaker_search_dialog

        return init_speaker_search_dialog()

    @staticmethod
    def _release_speaker_search_dialog() -> None:
        from lib.script.ui.speaker_search_dialog import cleanup_speaker_search_dialog

        cleanup_speaker_search_dialog()

    @staticmethod
    def _preload_cloudmusic_login_dialog():
        from lib.script.ui.cloudmusic_login_dialog import init_cloudmusic_login_dialog

        return init_cloudmusic_login_dialog()

    @staticmethod
    def _release_cloudmusic_login_dialog() -> None:
        from lib.script.ui.cloudmusic_login_dialog import cleanup_cloudmusic_login_dialog

        cleanup_cloudmusic_login_dialog()

    @staticmethod
    def _preload_yuanbao_login_dialog():
        from lib.script.ui.yuanbao_login_dialog import init_yuanbao_login_dialog

        return init_yuanbao_login_dialog()

    @staticmethod
    def _release_yuanbao_login_dialog() -> None:
        from lib.script.ui.yuanbao_login_dialog import cleanup_yuanbao_login_dialog

        cleanup_yuanbao_login_dialog()


def preload_runtime_ui() -> UiPreloader:
    """初始化就绪后的分步预加载（原行为）。"""
    preloader = UiPreloader()
    preloader.start()
    return preloader


def prewarm_runtime_ui_cache() -> UiPreloader | None:
    """启动等待期抢跑预绘制 UI 缓存。

    只有「系统调度 → 启动 → 启动期预绘制缓存」打开时才创建并启动预加载器，
    让构造与预绘制落在启动等待的空转里；开关关闭时返回 `None`，调用方保持原分步预加载。
    """
    if not ui_cache_preload_enabled():
        return None
    preloader = UiPreloader()
    preloader.start()
    return preloader
