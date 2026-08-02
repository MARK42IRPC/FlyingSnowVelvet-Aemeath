import os
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtWidgets import QApplication

from lib.core.qt_bridge.application_runtime import QtApplicationRuntime
from lib.script.main import (
    ApplicationState,
    _SHUTDOWN_FORCE_TIMEOUT_MS,
    _SHUTDOWN_QUIT_RETRY_MS,
)


class _RuntimeProbe:
    def __init__(self):
        self.scheduled = []
        self.processed = []
        self.exit_requests = []
        self.closed = []

    def schedule_once(self, delay_ms, callback):
        self.scheduled.append((delay_ms, callback))

    def process_events(self, application):
        self.processed.append(application)

    def request_exit(self, application, exit_code):
        self.exit_requests.append((application, exit_code))

    def close_all_windows(self, application):
        self.closed.append(application)


class ApplicationShutdownTests(unittest.TestCase):
    @staticmethod
    def _state_with_app(runtime=None):
        state = ApplicationState.__new__(ApplicationState)
        state._application_runtime = runtime or _RuntimeProbe()
        state._app = Mock()
        state._exit_code = 7
        state._runtime_exit_requested = False
        state._runtime_exit_acknowledged = False
        return state

    def test_quit_step_flushes_deferred_deletes_and_exits_event_loop(self):
        state = self._state_with_app()

        state._shutdown_quit_application()

        self.assertTrue(state._runtime_exit_requested)
        self.assertEqual(
            state._application_runtime.scheduled,
            [(_SHUTDOWN_QUIT_RETRY_MS, state._retry_runtime_exit)],
        )
        self.assertEqual(
            state._application_runtime.exit_requests,
            [(state._app, 7)],
        )

    def test_real_qt_event_loop_returns_requested_exit_code(self):
        app = QApplication.instance() or QApplication([])
        runtime = QtApplicationRuntime()
        state = self._state_with_app(runtime)
        state._app = app
        callback = state._on_runtime_exit_acknowledged
        app.aboutToQuit.connect(callback)
        try:
            runtime.schedule_once(0, state._shutdown_quit_application)
            returned = runtime.run_event_loop(app)
        finally:
            app.aboutToQuit.disconnect(callback)

        self.assertEqual(returned, 7)
        self.assertTrue(state._runtime_exit_acknowledged)

    def test_retry_closes_residual_windows_and_exits_again(self):
        state = self._state_with_app()

        state._retry_runtime_exit()

        self.assertEqual(state._application_runtime.closed, [state._app])
        self.assertEqual(
            state._application_runtime.exit_requests,
            [(state._app, 7)],
        )

    def test_runtime_exit_acknowledgement_is_recorded(self):
        state = self._state_with_app()

        state._on_runtime_exit_acknowledged()

        self.assertTrue(state._runtime_exit_acknowledged)

    def test_pending_events_are_delegated_to_runtime(self):
        state = self._state_with_app()

        state._process_pending_events()

        self.assertEqual(state._application_runtime.processed, [state._app])

    def test_process_watchdog_is_owned_and_cancellable(self):
        state = self._state_with_app()
        state._shutdown_force_quit_armed = False
        state._shutdown_force_timer = None
        state._shutdown_clean_exit_confirmed = False

        with patch("lib.script.main.threading.Timer") as timer_factory:
            state._arm_force_quit_fallback()
            timer = timer_factory.return_value

            timer_factory.assert_called_once_with(
                _SHUTDOWN_FORCE_TIMEOUT_MS / 1000.0,
                state._force_quit_if_still_pending,
            )
            self.assertTrue(timer.daemon)
            timer.start.assert_called_once_with()

            state._cancel_force_quit_fallback()

        timer.cancel.assert_called_once_with()
        self.assertFalse(state._shutdown_force_quit_armed)
        self.assertIsNone(state._shutdown_force_timer)

    def test_thread_drain_reports_a_lingering_non_daemon_thread(self):
        release = threading.Event()
        worker = threading.Thread(
            target=release.wait,
            name="shutdown-test-worker",
            daemon=False,
        )
        worker.start()
        try:
            remaining = ApplicationState._wait_for_non_daemon_threads(0.01)
            self.assertIn("shutdown-test-worker", remaining)
        finally:
            release.set()
            worker.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
