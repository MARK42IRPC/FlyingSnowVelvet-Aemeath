"""Qt-based local music player for cloudmusic."""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt5.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer


class QtMusicPlayer(QObject):
    """Drive local music playback on the Qt main thread via QMediaPlayer."""

    play_requested = pyqtSignal(str, float, int)
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    volume_requested = pyqtSignal(float)
    seek_requested = pyqtSignal(int)

    playback_started = pyqtSignal(int)
    playback_finished = pyqtSignal(int)
    playback_error = pyqtSignal(int, str)
    duration_changed = pyqtSignal(int, int)

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

    def position_ms(self) -> int:
        with self._lock:
            return self._position_ms

    def duration_ms_value(self) -> int:
        with self._lock:
            return self._duration_ms

    def is_busy(self) -> bool:
        with self._lock:
            return self._is_playing or self._is_paused

    def backend_ready(self) -> bool:
        return self._backend_ready

    @pyqtSlot(str, float, int)
    def _play(self, path_str: str, volume: float, generation: int) -> None:
        if self._player.isAvailable() is False:
            self._backend_ready = False
            self.playback_error.emit(generation, "QtMultimedia 后端不可用")
            return
        path = Path(path_str)
        if not path.is_file():
            self.playback_error.emit(generation, f"文件不存在: {path}")
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
            self.playback_started.emit(generation)

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
            self.playback_finished.emit(generation)

    def _on_position_changed(self, position_ms: int) -> None:
        with self._lock:
            self._position_ms = max(0, int(position_ms))

    def _on_duration_changed(self, duration_ms: int) -> None:
        generation = 0
        value = max(0, int(duration_ms))
        with self._lock:
            self._duration_ms = value
            generation = self._generation
        self.duration_changed.emit(generation, value)

    def _on_error(self, _error_code: int) -> None:
        with self._lock:
            generation = self._generation
            self._is_playing = False
            self._is_paused = False
        message = self._player.errorString() or "Qt 多媒体播放失败"
        self.playback_error.emit(generation, message)
