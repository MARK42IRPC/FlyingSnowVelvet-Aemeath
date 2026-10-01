"""Cooperative owner-thread loop used by the diagnostic DirectX runtime."""
from __future__ import annotations

import heapq
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol


class DxLoopPoller(Protocol):
    """Native host that can drain pending events without blocking."""

    def poll_events(self) -> object:
        """Drain currently pending native events."""


class DxScheduledCall:
    """Cancellable one-shot callback owned by a :class:`DxLoopContext`."""

    def __init__(
        self,
        context: "DxLoopContext",
        deadline: float,
        callback: Callable[[], None],
    ) -> None:
        self._context = context
        self._deadline = deadline
        self._callback: Callable[[], None] | None = callback
        self._pending = True

    @property
    def pending(self) -> bool:
        with self._context._lock:
            return self._pending

    def cancel(self) -> None:
        with self._context._lock:
            if not self._pending:
                return
            self._pending = False
            self._callback = None
            self._context._wake.set()

    def _invoke(self) -> bool:
        with self._context._lock:
            if not self._pending or self._callback is None:
                return False
            self._pending = False
            callback, self._callback = self._callback, None
        callback()
        return True


class DxLoopContext:
    """Thread-safe queue and timer loop whose callbacks run on one owner thread."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        exception_handler: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._clock = clock
        self._exception_handler = exception_handler or self._report_unhandled_exception
        self._owner_thread_id = threading.get_ident()
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._queued: deque[Callable[[], None]] = deque()
        self._timers: list[tuple[float, int, DxScheduledCall]] = []
        self._next_sequence = 0
        self._pollers: dict[int, DxLoopPoller] = {}
        self._running = False
        self._exit_requested = False
        self._exit_code = 0

    @staticmethod
    def _report_unhandled_exception(error: BaseException) -> None:
        sys.excepthook(type(error), error, error.__traceback__)

    def _invoke_callback(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as exc:
            self._exception_handler(exc)

    @property
    def owner_thread_id(self) -> int:
        return self._owner_thread_id

    @property
    def exit_requested(self) -> bool:
        with self._lock:
            return self._exit_requested

    @property
    def exit_code(self) -> int:
        with self._lock:
            return self._exit_code

    def now(self) -> float:
        return float(self._clock())

    def assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise RuntimeError("DX event loop operations must run on the owner thread")

    def post(self, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            self._queued.append(callback)
            self._wake.set()

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> DxScheduledCall:
        delay_seconds = max(0, int(delay_ms)) / 1000.0
        return self.call_at(self.now() + delay_seconds, callback)

    def call_at(self, deadline: float, callback: Callable[[], None]) -> DxScheduledCall:
        if not callable(callback):
            raise TypeError("callback must be callable")
        call = DxScheduledCall(self, float(deadline), callback)
        with self._lock:
            sequence = self._next_sequence
            self._next_sequence += 1
            heapq.heappush(self._timers, (call._deadline, sequence, call))
            self._wake.set()
        return call

    def register_poller(self, poller: DxLoopPoller) -> None:
        self.assert_owner_thread()
        if not callable(getattr(poller, "poll_events", None)):
            raise TypeError("poller must provide poll_events()")
        with self._lock:
            self._pollers[id(poller)] = poller
            self._wake.set()

    def unregister_poller(self, poller: DxLoopPoller) -> None:
        with self._lock:
            self._pollers.pop(id(poller), None)
            self._wake.set()

    def registered_pollers(self) -> tuple[DxLoopPoller, ...]:
        with self._lock:
            return tuple(self._pollers.values())

    def request_exit(self, exit_code: int = 0) -> None:
        with self._lock:
            if not self._exit_requested:
                self._exit_requested = True
                self._exit_code = int(exit_code)
            self._wake.set()

    def _discard_inactive_timers_locked(self) -> None:
        while self._timers and not self._timers[0][2]._pending:
            heapq.heappop(self._timers)

    def _take_due_calls(self) -> tuple[DxScheduledCall, ...]:
        due: list[DxScheduledCall] = []
        now = self.now()
        with self._lock:
            self._discard_inactive_timers_locked()
            while self._timers and self._timers[0][0] <= now:
                _, _, call = heapq.heappop(self._timers)
                if call._pending:
                    due.append(call)
                self._discard_inactive_timers_locked()
        return tuple(due)

    def _dispatch_ready(self) -> int:
        with self._lock:
            callbacks = tuple(self._queued)
            self._queued.clear()
        delivered = 0
        for callback in callbacks:
            self._invoke_callback(callback)
            delivered += 1
        for call in self._take_due_calls():
            try:
                invoked = call._invoke()
            except Exception as exc:
                self._exception_handler(exc)
                invoked = True
            if invoked:
                delivered += 1
        return delivered

    def _poll_native_hosts(self) -> int:
        delivered = 0
        for poller in self.registered_pollers():
            is_alive = getattr(poller, "is_alive", None)
            if callable(is_alive) and not is_alive():
                self.unregister_poller(poller)
                continue
            events = poller.poll_events()
            if events:
                try:
                    delivered += len(events)
                except TypeError:
                    delivered += 1
        return delivered

    def _wait_timeout(self, max_wait_ms: int) -> float:
        limit = max(0, int(max_wait_ms)) / 1000.0
        with self._lock:
            self._wake.clear()
            self._discard_inactive_timers_locked()
            if self._queued or self._exit_requested:
                return 0.0
            if not self._timers:
                return limit
            timer_wait = max(0.0, self._timers[0][0] - self.now())
            return min(limit, timer_wait)

    def run_once(self, max_wait_ms: int = 0) -> int:
        """Process one bounded turn and return the number of delivered items."""
        self.assert_owner_thread()
        delivered = self._dispatch_ready()
        if not self.exit_requested:
            delivered += self._poll_native_hosts()
        if delivered or self.exit_requested or int(max_wait_ms) <= 0:
            return delivered

        self._wake.wait(self._wait_timeout(max_wait_ms))
        delivered += self._dispatch_ready()
        if not self.exit_requested:
            delivered += self._poll_native_hosts()
        return delivered

    def run(self, *, idle_poll_interval_ms: int = 8) -> int:
        """Run until :meth:`request_exit` is accepted."""
        self.assert_owner_thread()
        with self._lock:
            if self._running:
                raise RuntimeError("DX event loop is already running")
            self._running = True
        try:
            while not self.exit_requested:
                self.run_once(max(1, int(idle_poll_interval_ms)))
            return self.exit_code
        finally:
            with self._lock:
                self._running = False


__all__ = ["DxLoopContext", "DxLoopPoller", "DxScheduledCall"]
