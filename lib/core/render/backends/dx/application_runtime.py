"""Qt-free application lifecycle backed by the DirectX loop context."""
from __future__ import annotations

import threading
import sys
from collections.abc import Callable

from lib.core.application_runtime import ApplicationRuntime

from .loop import DxLoopContext, DxLoopPoller


class DxApplication:
    """Minimal application state for one diagnostic DirectX event loop."""

    def __init__(self, logger: object, argv: list[str]) -> None:
        self.logger = logger
        self.argv = tuple(argv)
        self._exit_callbacks: list[Callable[[], None]] = []
        self._exit_acknowledged = False
        self._lock = threading.RLock()

    def connect_exit_acknowledged(self, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            if not self._exit_acknowledged:
                self._exit_callbacks.append(callback)

    def acknowledge_exit(self) -> None:
        with self._lock:
            if self._exit_acknowledged:
                return
            self._exit_acknowledged = True
            callbacks, self._exit_callbacks = self._exit_callbacks, []
        for callback in callbacks:
            callback()


class DxApplicationRuntime(ApplicationRuntime):
    """Drive queued work, timers and registered native hosts without Qt."""

    def __init__(
        self,
        context: DxLoopContext | None = None,
        *,
        native_poll_interval_ms: int = 8,
    ) -> None:
        self.context = context or DxLoopContext()
        self._native_poll_interval_ms = max(1, int(native_poll_interval_ms))
        self._application: DxApplication | None = None

    def _require_application(self, application: object) -> DxApplication:
        if application is not self._application or not isinstance(application, DxApplication):
            raise ValueError("application does not belong to this DX runtime")
        return application

    def create_application(
        self,
        logger: object,
        argv: list[str] | None = None,
    ) -> DxApplication:
        self.context.assert_owner_thread()
        if self._application is not None:
            raise RuntimeError("DX application has already been created")
        self._application = DxApplication(logger, sys.argv if argv is None else argv)
        return self._application

    def connect_exit_acknowledged(
        self,
        application: object,
        callback: Callable[[], None],
    ) -> None:
        self._require_application(application).connect_exit_acknowledged(callback)

    def schedule_once(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self.context.call_later(delay_ms, callback)

    def run_event_loop(self, application: object) -> int:
        app = self._require_application(application)
        exit_code = self.context.run(
            idle_poll_interval_ms=self._native_poll_interval_ms,
        )
        app.acknowledge_exit()
        return exit_code

    def process_events(self, application: object) -> None:
        self._require_application(application)
        self.context.run_once(0)

    def request_exit(self, application: object, exit_code: int) -> None:
        self._require_application(application)
        self.context.request_exit(exit_code)

    def close_all_windows(self, application: object) -> None:
        self._require_application(application)
        self.context.assert_owner_thread()
        first_error: BaseException | None = None
        for host in self.context.registered_pollers():
            try:
                close = getattr(host, "close", None)
                if not callable(close):
                    close = getattr(host, "cleanup", None)
                if callable(close):
                    close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            finally:
                self.context.unregister_poller(host)
        if first_error is not None:
            raise first_error

    def register_window_host(self, host: DxLoopPoller) -> None:
        self.context.register_poller(host)

    def unregister_window_host(self, host: DxLoopPoller) -> None:
        self.context.unregister_poller(host)


__all__ = ["DxApplication", "DxApplicationRuntime"]
