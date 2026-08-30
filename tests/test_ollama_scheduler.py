import unittest
import threading
from unittest.mock import Mock, patch

from lib.core.event.center import Event, EventType
from lib.script.chat.ollama_bootstrap import OllamaBootstrapMixin
from tests.timing_fakes import FakeScheduler


class _Hub:
    def __init__(self):
        self.calls = []

    def submit_latest(self, key, callback, **kwargs):
        self.calls.append((key, callback, kwargs))
        return object()


class _Dispatcher:
    def __init__(self):
        self.calls = []
        self.cleaned = False

    def dispatch(self, callback, *args):
        self.calls.append((callback, args))

    def cleanup(self):
        self.cleaned = True


class _Manager(OllamaBootstrapMixin):
    def __init__(self):
        self._api_type = "ollama"
        self._scheduler = FakeScheduler()
        self._callback_dispatcher = _Dispatcher()
        self._ping_timer = None
        self._is_running = False

    def _ping(self):
        pass

    def _on_status_ready(self, is_running, models):
        pass


class _CleanupManager(OllamaBootstrapMixin):
    def __init__(self):
        self._shutdown_requested = threading.Event()
        self._is_running = True
        self._ping_timer = None
        self._scheduler = None
        self._callback_dispatcher = _Dispatcher()
        self._chat_state_lock = threading.Lock()
        self._chat_callbacks = {1: object()}
        self._chat_chunk_callbacks = {1: object()}
        self._api_rate_lock = threading.Lock()
        self._api_request_timestamps = [1.0]
        self._pull_response_lock = threading.Lock()
        self._pull_response = None
        self._ollama_proc_lock = threading.Lock()
        self._started_ollama = False
        self._ollama_process = None
        self._event_center = Mock()

    def _apply_status_direct(self, _response):
        pass


class OllamaSchedulerTests(unittest.TestCase):
    def test_app_main_starts_protocol_timer_without_qtimer(self):
        manager = _Manager()
        hub = _Hub()

        with patch("lib.script.chat.ollama_bootstrap.get_compute_hub", return_value=hub):
            manager._on_app_main(Event(EventType.APP_MAIN, {}))

        self.assertEqual(len(manager._scheduler.timers), 1)
        self.assertTrue(manager._scheduler.timers[0].active)
        self.assertEqual(manager._scheduler.timers[0].interval_ms, 5000)
        self.assertEqual(hub.calls[0][0], "ollama_ping")

    def test_local_mode_requires_injected_scheduler(self):
        manager = _Manager()
        manager._scheduler = None

        with patch("lib.script.chat.ollama_bootstrap.get_compute_hub", return_value=_Hub()):
            with self.assertRaisesRegex(RuntimeError, "需要注入 Scheduler"):
                manager._on_app_main(Event(EventType.APP_MAIN, {}))

    def test_cleanup_stops_owned_process_after_switching_to_api_mode(self):
        manager = _CleanupManager()
        manager._use_api_key = True
        process = Mock()
        process.poll.return_value = None
        manager._started_ollama = True
        manager._ollama_process = process

        manager.cleanup()
        manager.cleanup()

        self.assertTrue(manager._shutdown_requested.is_set())
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)
        process.kill.assert_not_called()
        self.assertFalse(manager._started_ollama)
        self.assertIsNone(manager._ollama_process)

    def test_shutdown_does_not_stop_an_unowned_process(self):
        manager = _CleanupManager()
        process = Mock()
        manager._ollama_process = process

        manager._shutdown_started_ollama()

        process.poll.assert_not_called()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_start_race_stops_process_created_after_shutdown_request(self):
        manager = _CleanupManager()
        process = Mock()
        process.poll.return_value = None

        def create_process(*_args, **_kwargs):
            manager._shutdown_requested.set()
            return process

        with patch(
            "lib.script.chat.ollama_bootstrap.requests.get",
            side_effect=RuntimeError("not running"),
        ), patch(
            "lib.script.chat.ollama_bootstrap.subprocess.Popen",
            side_effect=create_process,
        ):
            manager._try_start_ollama()

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)
        self.assertFalse(manager._started_ollama)
        self.assertIsNone(manager._ollama_process)


if __name__ == "__main__":
    unittest.main()
