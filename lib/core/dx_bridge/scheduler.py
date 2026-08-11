"""Qt-free timer adapters driven by the DirectX loop context."""
from __future__ import annotations

import threading
from collections.abc import Callable

from lib.core.timing.scheduler import PeriodicTimer, Scheduler

from .loop import DxLoopContext, DxScheduledCall


class DxPeriodicTimer:
    """Coalescing periodic timer that always delivers on the loop owner thread."""

    def __init__(self, context: DxLoopContext, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._context = context
        self._callback: Callable[[], None] | None = callback
        self._interval_ms = 0
        self._active = False
        self._cleaned = False
        self._generation = 0
        self._call: DxScheduledCall | None = None
        self._lock = threading.RLock()

    def _cancel_locked(self) -> None:
        if self._call is not None:
            self._call.cancel()
            self._call = None

    def _arm_locked(self, generation: int) -> None:
        self._call = self._context.call_later(
            self._interval_ms,
            lambda: self._on_timeout(generation),
        )

    def _on_timeout(self, generation: int) -> None:
        with self._lock:
            if (
                self._cleaned
                or not self._active
                or generation != self._generation
                or self._callback is None
            ):
                return
            self._call = None
            callback = self._callback
        try:
            callback()
        finally:
            with self._lock:
                if (
                    not self._cleaned
                    and self._active
                    and generation == self._generation
                ):
                    self._arm_locked(generation)

    def start(self, interval_ms: int) -> None:
        with self._lock:
            if self._cleaned:
                raise RuntimeError("timer has been cleaned up")
            self._interval_ms = max(1, int(interval_ms))
            self._active = True
            self._generation += 1
            self._cancel_locked()
            self._arm_locked(self._generation)

    def stop(self) -> None:
        with self._lock:
            if self._cleaned:
                return
            self._active = False
            self._generation += 1
            self._cancel_locked()

    def set_interval(self, interval_ms: int) -> None:
        with self._lock:
            if self._cleaned:
                raise RuntimeError("timer has been cleaned up")
            self._interval_ms = max(1, int(interval_ms))
            if self._active:
                self._generation += 1
                self._cancel_locked()
                self._arm_locked(self._generation)

    @property
    def interval_ms(self) -> int:
        with self._lock:
            return self._interval_ms

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active and not self._cleaned

    def cleanup(self) -> None:
        with self._lock:
            if self._cleaned:
                return
            self._cleaned = True
            self._active = False
            self._generation += 1
            self._cancel_locked()
            self._callback = None


class DxScheduler:
    """Own periodic timers attached to one DirectX loop context."""

    def __init__(self, context: DxLoopContext) -> None:
        self._context = context
        self._timers: list[DxPeriodicTimer] = []
        self._cleaned = False
        self._lock = threading.RLock()

    def create_periodic_timer(
        self,
        callback: Callable[[], None],
    ) -> PeriodicTimer:
        with self._lock:
            if self._cleaned:
                raise RuntimeError("scheduler has been cleaned up")
            timer = DxPeriodicTimer(self._context, callback)
            self._timers.append(timer)
            return timer

    def cleanup(self) -> None:
        with self._lock:
            if self._cleaned:
                return
            self._cleaned = True
            timers, self._timers = self._timers, []
        for timer in timers:
            timer.cleanup()


def create_scheduler(context: DxLoopContext) -> Scheduler:
    return DxScheduler(context)


__all__ = ["DxPeriodicTimer", "DxScheduler", "create_scheduler"]
