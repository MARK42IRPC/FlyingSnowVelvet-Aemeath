import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.core.event.center import Event, EventType
from lib.script.chat.handler import ChatHandler
from tests.timing_fakes import FakeScheduler


class _FakeEventCenter:
    def __init__(self):
        self.subscriptions = []
        self.published = []

    def subscribe(self, event_type, callback):
        self.subscriptions.append((event_type, callback))

    def unsubscribe(self, event_type, callback):
        item = (event_type, callback)
        if item in self.subscriptions:
            self.subscriptions.remove(item)

    def publish(self, event):
        self.published.append(event)


class _FakeOllama:
    use_api_key_mode = True
    is_running = True
    is_chat_busy = False
    strict_mode_enabled = False
    mode_error_message = ""

    def __init__(self):
        self.calls = []
        self.auto_timer_active_during_call = None
        self.handler = None

    def stream_chat(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("quiet_throttled") and self.handler is not None:
            self.auto_timer_active_during_call = self.handler._auto_timer.active


class _FakeScreenCapture:
    def __init__(self, image_data: bytes | None = b"png"):
        self.image_data = image_data
        self.calls = 0

    def capture_primary_png(self):
        self.calls += 1
        return self.image_data


class ChatSchedulerTests(unittest.TestCase):
    def _create_handler(self):
        center = _FakeEventCenter()
        ollama = _FakeOllama()
        scheduler = FakeScheduler()
        patches = [
            patch("lib.script.chat.handler.get_event_center", return_value=center),
            patch("lib.script.chat.handler.get_ollama_manager", return_value=ollama),
            patch.object(ChatHandler, "_load_persona", return_value="persona"),
        ]
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        screen_capture = _FakeScreenCapture()
        handler = ChatHandler(
            scheduler=scheduler,
            screen_capture=screen_capture,
        )
        ollama.handler = handler
        self.addCleanup(handler.cleanup)
        return handler, center, ollama, scheduler, screen_capture

    def test_stream_flush_uses_protocol_timer_as_single_shot(self):
        handler, center, _, scheduler, _ = self._create_handler()
        stream_timer = scheduler.timers[0]

        handler._on_stream_chunk("a")
        handler._on_stream_chunk("ab")

        self.assertTrue(stream_timer.active)
        self.assertEqual(stream_timer.interval_ms, 40)

        stream_timer.fire()

        self.assertFalse(stream_timer.active)
        self.assertEqual(handler._stream_pending_raw, "")
        self.assertEqual(center.published[-1].data["text"], "ab")

    def test_auto_companion_stops_timer_before_work_and_reschedules(self):
        handler, _, ollama, scheduler, screen_capture = self._create_handler()
        auto_timer = scheduler.timers[1]

        with patch(
            "lib.script.chat.handler_auto_companion._get_effective_auto_companion_interval_ms",
            return_value=(60000, 60000),
        ):
            handler._on_app_main(Event(EventType.APP_MAIN, {}))
            auto_timer.fire()

        self.assertEqual(auto_timer.interval_ms, 60000)
        self.assertTrue(auto_timer.active)
        self.assertFalse(ollama.auto_timer_active_during_call)
        self.assertEqual(len(ollama.calls), 1)
        self.assertEqual(screen_capture.calls, 1)

    def test_cleanup_is_idempotent_and_releases_scheduler(self):
        handler, center, _, scheduler, _ = self._create_handler()

        handler.cleanup()
        handler.cleanup()

        self.assertEqual(center.subscriptions, [])
        self.assertTrue(scheduler.cleaned)
        self.assertTrue(all(timer.cleaned for timer in scheduler.timers))

    def test_chat_scheduler_modules_do_not_declare_qt_imports(self):
        repo_root = Path(__file__).resolve().parents[1]
        module_paths = [
            repo_root / "lib/script/chat/handler.py",
            repo_root / "lib/script/chat/handler_auto_companion.py",
            repo_root / "lib/script/chat/ollama_bootstrap.py",
        ]
        forbidden = []
        for module_path in module_paths:
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    imported = node.module or ""
                    if imported.startswith(("PyQt5", "lib.core.qt_bridge")):
                        forbidden.append((module_path.name, imported))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith(("PyQt5", "lib.core.qt_bridge")):
                            forbidden.append((module_path.name, alias.name))

        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
