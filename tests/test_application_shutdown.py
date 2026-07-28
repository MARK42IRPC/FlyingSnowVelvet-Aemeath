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

from PyQt5.QtCore import QEvent, QTimer
from PyQt5.QtWidgets import QApplication

from lib.script.main import (
    ApplicationState,
    _SHUTDOWN_FORCE_TIMEOUT_MS,
    _SHUTDOWN_QUIT_RETRY_MS,
)


class ApplicationShutdownTests(unittest.TestCase):
    @staticmethod
    def _state_with_app():
        state = ApplicationState.__new__(ApplicationState)
        state._app = Mock()
        state._exit_code = 7
        state._qt_exit_requested = False
        state._qt_exit_acknowledged = False
        return state

    def test_quit_step_flushes_deferred_deletes_and_exits_event_loop(self):
        state = self._state_with_app()

        with patch("lib.script.main.QTimer.singleShot") as single_shot:
            state._shutdown_quit_application()

        self.assertTrue(state._qt_exit_requested)
        single_shot.assert_called_once_with(_SHUTDOWN_QUIT_RETRY_MS, state._retry_qt_exit)
        state._app.sendPostedEvents.assert_called_once_with(None, QEvent.DeferredDelete)
        state._app.exit.assert_called_once_with(7)
        state._app.quit.assert_not_called()

    def test_real_qt_event_loop_returns_requested_exit_code(self):
        app = QApplication.instance() or QApplication([])
        state = self._state_with_app()
        state._app = app
        callback = state._on_qt_about_to_quit
        app.aboutToQuit.connect(callback)
        try:
            QTimer.singleShot(0, state._shutdown_quit_application)
            returned = app.exec_()
        finally:
            app.aboutToQuit.disconnect(callback)

        self.assertEqual(returned, 7)
        self.assertTrue(state._qt_exit_acknowledged)

    def test_retry_closes_residual_windows_and_exits_again(self):
        state = self._state_with_app()

        state._retry_qt_exit()

        state._app.closeAllWindows.assert_called_once_with()
        state._app.sendPostedEvents.assert_called_once_with(None, QEvent.DeferredDelete)
        state._app.exit.assert_called_once_with(7)

    def test_about_to_quit_acknowledges_qt_exit(self):
        state = self._state_with_app()

        state._on_qt_about_to_quit()

        self.assertTrue(state._qt_exit_acknowledged)

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
