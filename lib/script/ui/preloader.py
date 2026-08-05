'''Staged startup preloading for lazily-created UI windows.'''

from __future__ import annotations

import time
from collections.abc import Callable

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtWidgets import QApplication

from lib.core.logger import get_logger

_logger = get_logger(__name__)


class UiPreloader(QObject):
    def __init__(self, parent=None) -> None:
        super().__init__(parent or QApplication.instance())
        self._started = False
        self._steps: tuple[tuple[str, Callable[[], None]], ...] = (
            ('playlist_panel', self._preload_playlist_panel),
            ('progress_panel', self._preload_progress_panel),
            ('speaker_search_dialog', self._preload_speaker_search_dialog),
            ('cloudmusic_login_dialog', self._preload_cloudmusic_login_dialog),
            ('yuanbao_login_dialog', self._preload_yuanbao_login_dialog),
        )
        self._step_index = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._run_next)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._timer.start(0)

    def stop(self) -> None:
        self._timer.stop()

    def _run_next(self) -> None:
        if self._step_index >= len(self._steps):
            return

        name, preload = self._steps[self._step_index]
        self._step_index += 1
        started_at = time.perf_counter()
        try:
            preload()
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            _logger.debug('[ui.preload] %s ready in %.1f ms', name, elapsed_ms)
        except Exception:
            _logger.exception('[ui.preload] failed: %s', name)

        if self._step_index < len(self._steps):
            self._timer.start(0)

    @staticmethod
    def _preload_playlist_panel() -> None:
        from lib.script.ui.playlist_panel import init_playlist_panel

        init_playlist_panel()

    @staticmethod
    def _preload_progress_panel() -> None:
        from lib.script.ui.progress_panel import init_progress_panel

        init_progress_panel()

    @staticmethod
    def _preload_speaker_search_dialog() -> None:
        from lib.script.ui.speaker_search_dialog import init_speaker_search_dialog

        init_speaker_search_dialog()

    @staticmethod
    def _preload_cloudmusic_login_dialog() -> None:
        from lib.script.ui.cloudmusic_login_dialog import init_cloudmusic_login_dialog

        init_cloudmusic_login_dialog()

    @staticmethod
    def _preload_yuanbao_login_dialog() -> None:
        from lib.script.ui.yuanbao_login_dialog import init_yuanbao_login_dialog

        init_yuanbao_login_dialog()

def preload_runtime_ui() -> UiPreloader:
    preloader = UiPreloader()
    preloader.start()
    return preloader
