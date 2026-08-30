import os
import threading
import unittest
from types import SimpleNamespace
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

    def test_primary_window_shutdown_uses_host_lifecycle_contract(self):
        state = ApplicationState.__new__(ApplicationState)
        state._tray_host = Mock()
        state._announcement_controller = None
        state._pet = Mock()
        state._process_pending_events = Mock()
        tray_host = state._tray_host
        pet_host = state._pet

        state._shutdown_stop_primary_windows()

        tray_host.disconnect_quit_requested.assert_called_once_with(state._on_tray_quit)
        tray_host.disconnect_announcement_requested.assert_called_once_with(
            state._on_tray_announcement
        )
        pet_host.shutdown_host.assert_called_once_with()
        self.assertIsNone(state._pet)
        state._process_pending_events.assert_called_once_with()

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

    def test_backend_cleanup_runs_once_after_event_loop(self):
        state = ApplicationState.__new__(ApplicationState)
        state._backend_cleanup = Mock()
        state._backend_cleaned = False

        state._cleanup_backend()
        state._cleanup_backend()

        state._backend_cleanup.assert_called_once_with()

    def test_component_cleanup_continues_and_only_retries_failed_steps(self):
        state = ApplicationState.__new__(ApplicationState)
        state._components_cleaned = False
        state._component_cleanup_steps_completed = set()
        state._application_ui = SimpleNamespace(stop_runtime=Mock())
        state._managers = {}
        state._cleanup_handler = None
        failing_cleanup = Mock(side_effect=(RuntimeError("cleanup failed"), None))
        other_names = (
            "cleanup_all_managers",
            "cleanup_stream_memory",
            "cleanup_tool_dispatcher",
            "cleanup_office_service",
            "cleanup_interaction_mode_service",
            "cleanup_game_mode_service",
            "cleanup_ollama_manager",
            "cleanup_cmd_center",
            "cleanup_voice_request_handler",
            "cleanup_gsvmove_service",
            "cleanup_bug_tracker_service",
            "cleanup_microphone_push_to_talk_manager",
            "cleanup_microphone_stt_service",
        )
        other_cleanups = {name: Mock() for name in other_names}

        with patch.multiple(
            "lib.script.main",
            cleanup_chat_handler=failing_cleanup,
            **other_cleanups,
        ):
            state._perform_component_cleanup(skip_visual_cleanup=True)
            self.assertFalse(state._components_cleaned)
            self.assertEqual(failing_cleanup.call_count, 1)
            self.assertTrue(all(cleanup.call_count == 1 for cleanup in other_cleanups.values()))

            state._perform_component_cleanup(skip_visual_cleanup=True)

        self.assertTrue(state._components_cleaned)
        self.assertEqual(failing_cleanup.call_count, 2)
        self.assertTrue(all(cleanup.call_count == 1 for cleanup in other_cleanups.values()))
        state._application_ui.stop_runtime.assert_called_once_with()

    def test_main_releases_lock_and_cleans_backend_when_state_creation_fails(self):
        backend_cleanup = Mock()
        bundle = SimpleNamespace(cleanup=backend_cleanup)

        with patch("lib.script.main._new_acquire_single_instance_lock", return_value=True), patch(
            "lib.script.main._new_release_single_instance_lock"
        ) as release_lock, patch(
            "lib.script.main.ApplicationState",
            side_effect=RuntimeError("state init failed"),
        ), patch("lib.script.main.cleanup_event_center") as cleanup_events:
            with self.assertRaises(SystemExit) as raised:
                from lib.script.main import main

                main(backend_bundle=bundle)

        self.assertEqual(raised.exception.code, -1)
        backend_cleanup.assert_called_once_with()
        cleanup_events.assert_called_once_with()
        release_lock.assert_called_once_with()

    def test_second_instance_notification_cleans_preconfigured_backend(self):
        backend_cleanup = Mock()
        bundle = SimpleNamespace(cleanup=backend_cleanup)

        with patch("lib.script.main._new_acquire_single_instance_lock", return_value=False), patch(
            "lib.script.main._new_notify_already_running"
        ) as notify:
            from lib.script.main import main

            result = main(backend_bundle=bundle)

        self.assertIsNone(result)
        notify.assert_called_once_with()
        backend_cleanup.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
