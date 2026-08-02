"""Thread-aware callback dispatch built on the backend-neutral EventPump."""
from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable

from lib.core.event.pump import EventPump, EventPumpFactory

logger = logging.getLogger(__name__)


class CallbackDispatcher:
    """Dispatch callbacks onto the thread that created this object."""

    def __init__(self, pump_factory: EventPumpFactory | None = None):
        self._owner_thread_id = threading.get_ident()
        self._pump_factory = pump_factory
        self._pump: EventPump | None = None
        self._queue = deque()
        self._lock = threading.Lock()
        self._scheduled = False
        self._cleaned = False
        # Bind the backend pump while still on the declared owner thread.
        # Lazy construction from the first producer would give a worker-owned
        # Qt object whose event loop may not exist after that task completes.
        self._ensure_pump()

    def dispatch(self, callback: Callable, *args, **kwargs) -> None:
        if self._cleaned:
            return
        if threading.get_ident() == self._owner_thread_id:
            self._invoke(callback, args, kwargs)
            return

        with self._lock:
            if self._cleaned:
                return
            self._queue.append((callback, args, kwargs))
            should_schedule = not self._scheduled
            if should_schedule:
                self._scheduled = True

        if should_schedule:
            pump = self._ensure_pump()
            if pump is not None:
                try:
                    pump.emit()
                    return
                except Exception as exc:
                    logger.debug("callback pump emit failed: %s", exc)
            self._drain()

    def _ensure_pump(self) -> EventPump | None:
        if self._pump is not None:
            return self._pump
        if threading.get_ident() != self._owner_thread_id:
            return None
        try:
            factory = self._pump_factory
            if factory is None:
                from lib.core.qt_bridge.event_pump import create_event_pump

                factory = create_event_pump
            self._pump = factory(self._drain)
        except Exception as exc:
            logger.debug("callback pump initialization failed: %s", exc)
            self._pump = None
        return self._pump

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._queue or self._cleaned:
                    self._scheduled = False
                    return
                callback, args, kwargs = self._queue.popleft()
            self._invoke(callback, args, kwargs)

    @staticmethod
    def _invoke(callback: Callable, args: tuple, kwargs: dict) -> None:
        try:
            callback(*args, **kwargs)
        except Exception:
            logger.exception("dispatched callback failed")

    def cleanup(self) -> None:
        with self._lock:
            if self._cleaned:
                return
            self._cleaned = True
            self._queue.clear()
            self._scheduled = False
        if self._pump is not None:
            try:
                self._pump.disconnect()
            except Exception:
                pass
            self._pump = None
