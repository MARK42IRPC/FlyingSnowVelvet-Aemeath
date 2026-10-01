"""Qt-based local music player for cloudmusic."""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt5.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer

from lib.core.logger import get_logger

logger = get_logger(__name__)


class QtMusicPlayer(QObject):
    """Drive local music playback on the Qt main thread via QMediaPlayer.

    Implements ``lib.core.services.music_playback.MusicPlayerProtocol``.
    Every command is queued onto the Qt main thread through a private signal, so
    callers may invoke the protocol methods from any thread.
    """

    play_requested = pyqtSignal(str, float, int)
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    volume_requested = pyqtSignal(float)
    seek_requested = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._generation = 0
        self._position_ms = 0
        self._duration_ms = 0
        self._is_playing = False
        self._is_paused = False
        self._start_emitted = False
        self._finish_emitted = False
        self._suppress_finish = False
        self._backend_ready = True
        self._callbacks: dict = {
            "started":  None,
            "finished": None,
            "error":    None,
            "duration": None,
        }

        self._player = QMediaPlayer(self)
        self._player.setNotifyInterval(250)
        self._player.stateChanged.connect(
            lambda state: self._on_state_changed(int(state))
        )
        self._player.mediaStatusChanged.connect(
            lambda status: self._on_media_status_changed(int(status))
        )
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.error.connect(lambda error_code: self._on_error(int(error_code)))

        self.play_requested.connect(self._play)
        self.pause_requested.connect(self._pause)
        self.resume_requested.connect(self._resume)
        self.stop_requested.connect(self._stop)
        self.volume_requested.connect(self._set_volume)
        self.seek_requested.connect(self._seek)

    # ── 回调──────────────────────────────────────────────────────────────

    def set_callbacks(
        self,
        *,
        on_started=None,
        on_finished=None,
        on_error=None,
        on_duration_changed=None,
    ) -> None:
        """注册回调，整体替换上一组；全部传 None 即解绑。"""
        with self._lock:
            self._callbacks = {
                "started":  on_started,
                "finished": on_finished,
                "error":    on_error,
                "duration": on_duration_changed,
            }

    def _emit(self, name: str, *args) -> None:
        """取出回调并在锁外调用，避免订阅方回查播放器时自锁。"""
        with self._lock:
            callback = self._callbacks.get(name)
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as e:
            logger.error("[QtMusicPlayer] %s 回调异常: %s", name, e)

    # ── 状态查询──────────────────────────────────────────────────────────

    def position_ms(self) -> int:
        with self._lock:
            return self._position_ms

    def duration_ms(self) -> int:
        with self._lock:
            return self._duration_ms

    def is_busy(self) -> bool:
        with self._lock:
            return self._is_playing or self._is_paused

    def backend_ready(self) -> bool:
        return self._backend_ready

    def cleanup(self) -> None:
        """停止播放并解绑回调；可重复调用。"""
        self._stop()
        self.set_callbacks()

    # ── 命令──────────────────────────────────────────────────────────────

    def play(self, path_str: str, volume: float, generation: int) -> None:
        self.play_requested.emit(str(path_str), float(volume), int(generation))

    def pause(self) -> None:
        self.pause_requested.emit()

    def resume(self) -> None:
        self.resume_requested.emit()

    def stop(self) -> None:
        self.stop_requested.emit()

    def set_volume(self, volume: float) -> None:
        self.volume_requested.emit(float(volume))

    def seek(self, position_ms: int) -> None:
        self.seek_requested.emit(int(position_ms))

    @pyqtSlot(str, float, int)
    def _play(self, path_str: str, volume: float, generation: int) -> None:
        if self._player.isAvailable() is False:
            self._backend_ready = False
            self._emit("error", generation, "QtMultimedia 后端不可用")
            return
        path = Path(path_str)
        if not path.is_file():
            self._emit("error", generation, f"文件不存在: {path}")
            return

        with self._lock:
            self._generation = generation
            self._position_ms = 0
            self._duration_ms = 0
            self._is_playing = False
            self._is_paused = False
            self._start_emitted = False
            self._finish_emitted = False
            self._suppress_finish = False

        self._player.stop()
        self._player.setMedia(QMediaContent())
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(str(path.resolve()))))
        self._player.setVolume(max(0, min(100, int(volume * 100))))
        self._player.play()

    @pyqtSlot()
    def _pause(self) -> None:
        if self._player.state() == QMediaPlayer.PlayingState:
            self._player.pause()

    @pyqtSlot()
    def _resume(self) -> None:
        if self._player.state() == QMediaPlayer.PausedState:
            self._player.play()

    @pyqtSlot()
    def _stop(self) -> None:
        with self._lock:
            self._suppress_finish = True
            self._finish_emitted = True
            self._is_playing = False
            self._is_paused = False
            self._position_ms = 0
        self._player.stop()
        self._player.setMedia(QMediaContent())

    @pyqtSlot(float)
    def _set_volume(self, volume: float) -> None:
        self._player.setVolume(max(0, min(100, int(volume * 100))))

    @pyqtSlot(int)
    def _seek(self, position_ms: int) -> None:
        self._player.setPosition(max(0, int(position_ms)))

    def _on_state_changed(self, state: int) -> None:
        emit_started = False
        generation = 0
        with self._lock:
            self._is_playing = state == QMediaPlayer.PlayingState
            self._is_paused = state == QMediaPlayer.PausedState
            generation = self._generation
            if self._is_playing and not self._start_emitted:
                self._start_emitted = True
                emit_started = True
        if emit_started:
            self._emit("started", generation)

    def _on_media_status_changed(self, status: int) -> None:
        emit_finished = False
        generation = 0
        with self._lock:
            if status == QMediaPlayer.EndOfMedia:
                if not self._suppress_finish and not self._finish_emitted:
                    self._finish_emitted = True
                    self._is_playing = False
                    self._is_paused = False
                    generation = self._generation
                    emit_finished = True
        if emit_finished:
            self._emit("finished", generation)

    def _on_position_changed(self, position_ms: int) -> None:
        with self._lock:
            self._position_ms = max(0, int(position_ms))

    def _on_duration_changed(self, duration_ms: int) -> None:
        generation = 0
        value = max(0, int(duration_ms))
        with self._lock:
            self._duration_ms = value
            generation = self._generation
        self._emit("duration", generation, value)

    def _on_error(self, _error_code: int) -> None:
        with self._lock:
            generation = self._generation
            self._is_playing = False
            self._is_paused = False
        message = self._player.errorString() or "Qt 多媒体播放失败"
        self._emit("error", generation, message)
