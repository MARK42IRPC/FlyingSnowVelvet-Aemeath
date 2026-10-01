"""Qt implementation of the backend-neutral scheduling contracts."""
from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QObject, QTimer

from lib.core.timing.scheduler import PeriodicTimer, Scheduler


class QtPeriodicTimer:
    """Small QTimer adapter that keeps Qt out of the timing core."""

    def __init__(self, parent: QObject, callback: Callable[[], None]):
        self._timer = QTimer(parent)
        self._callback = callback
        self._cleaned = False
        self._timer.timeout.connect(callback)

    def start(self, interval_ms: int) -> None:
        if self._cleaned:
            raise RuntimeError("timer has been cleaned up")
        self._timer.start(max(1, int(interval_ms)))

    def stop(self) -> None:
        if not self._cleaned:
            self._timer.stop()

    def set_interval(self, interval_ms: int) -> None:
        if self._cleaned:
            raise RuntimeError("timer has been cleaned up")
        self._timer.setInterval(max(1, int(interval_ms)))

    @property
    def interval_ms(self) -> int:
        return int(self._timer.interval())

    @property
    def active(self) -> bool:
        return bool(self._timer.isActive())

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self._timer.stop()
        try:
            self._timer.timeout.disconnect(self._callback)
        except (TypeError, RuntimeError):
            pass
        self._callback = None
        self._timer.deleteLater()


class QtScheduler(QObject):
    """Own QTimer instances for a single timing component."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._timers: list[QtPeriodicTimer] = []
        self._cleaned = False

    def create_periodic_timer(
        self,
        callback: Callable[[], None],
    ) -> PeriodicTimer:
        if self._cleaned:
            raise RuntimeError("scheduler has been cleaned up")
        timer = QtPeriodicTimer(self, callback)
        self._timers.append(timer)
        return timer

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        timers, self._timers = self._timers, []
        for timer in timers:
            timer.cleanup()


def create_scheduler(parent: QObject | None = None) -> Scheduler:
    """Create the Qt scheduler exposed to the composition boundary."""
    return QtScheduler(parent)


def call_later(delay_ms: int, callback: Callable[[], None]) -> None:
    """Schedule a one-shot callback on the Qt event loop."""
    QTimer.singleShot(max(0, int(delay_ms)), callback)
