"""Cross-thread event delivery for the diagnostic DirectX runtime."""
from __future__ import annotations

import threading
from collections.abc import Callable

from lib.core.event.pump import EventPump

from .loop import DxLoopContext


class DxEventPump:
    """Coalesce wakeups and deliver one callback on the loop owner thread."""

    def __init__(self, context: DxLoopContext, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._context = context
        self._callback: Callable[[], None] | None = callback
        self._pending = False
        self._lock = threading.Lock()

    def emit(self) -> None:
        with self._lock:
            if self._callback is None or self._pending:
                return
            self._pending = True
        self._context.post(self._deliver)

    def _deliver(self) -> None:
        with self._lock:
            self._pending = False
            callback = self._callback
        if callback is not None:
            callback()

    def disconnect(self) -> None:
        with self._lock:
            self._callback = None
            self._pending = False


def create_event_pump(
    callback: Callable[[], None],
    *,
    context: DxLoopContext,
) -> EventPump:
    return DxEventPump(context, callback)


__all__ = ["DxEventPump", "create_event_pump"]
