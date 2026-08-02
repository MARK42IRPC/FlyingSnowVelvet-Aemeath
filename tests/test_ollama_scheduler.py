import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
