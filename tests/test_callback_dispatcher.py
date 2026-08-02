import json
import subprocess
import sys
import threading
import textwrap
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

from lib.core.event.callbacks import CallbackDispatcher
from lib.script.chat.ollama_session import OllamaSessionMixin


class _DeferredPump:
    def __init__(self, callback):
        self.callback = callback
        self.emitted = 0
        self.disconnected = False

    def emit(self):
        self.emitted += 1

    def flush(self):
        self.callback()

    def disconnect(self):
        self.disconnected = True


def _make_chat_session(dispatcher):
    session = OllamaSessionMixin()
    session._api_type = "openai_compatible"
    session._is_running = True
    session._available_models = []
    session._use_api_key = True
    session._api_rate_lock = threading.Lock()
    session._api_request_timestamps = deque()
    session._chat_state_lock = threading.Lock()
    session._chat_request_id = 0
    session._chat_callbacks = {}
    session._chat_chunk_callbacks = {}
    session._callback_dispatcher = dispatcher
    return session


class CallbackDispatcherTests(unittest.TestCase):
    def test_pump_is_created_on_dispatcher_owner_thread(self):
        owner_thread_id = threading.get_ident()
        factory_thread_ids = []
        pumps = []

        def factory(callback):
            factory_thread_ids.append(threading.get_ident())
            pumps.append(_DeferredPump(callback))
            return pumps[-1]

        dispatcher = CallbackDispatcher(pump_factory=factory)
        worker = threading.Thread(target=lambda: dispatcher.dispatch(lambda: None))
        worker.start()
        worker.join(timeout=2)

        self.assertEqual(factory_thread_ids, [owner_thread_id])
        self.assertEqual(len(pumps), 1)
        dispatcher.cleanup()

    def test_background_callback_waits_for_owner_pump(self):
        pumps = []
        dispatcher = CallbackDispatcher(
            pump_factory=lambda callback: pumps.append(_DeferredPump(callback)) or pumps[-1],
        )
        received = []

        worker = threading.Thread(
            target=lambda: dispatcher.dispatch(received.append, "done"),
        )
        worker.start()
        worker.join(timeout=2)

        self.assertEqual(received, [])
        self.assertEqual(pumps[0].emitted, 1)
        pumps[0].flush()
        self.assertEqual(received, ["done"])

    def test_owner_thread_dispatch_is_immediate_and_cleanup_is_idempotent(self):
        pumps = []
        dispatcher = CallbackDispatcher(
            pump_factory=lambda callback: pumps.append(_DeferredPump(callback)) or pumps[-1],
        )
        received = []

        dispatcher.dispatch(received.append, "now")
        dispatcher.cleanup()
        dispatcher.cleanup()
        dispatcher.dispatch(received.append, "ignored")

        self.assertEqual(received, ["now"])

    def test_chat_completion_clears_busy_after_worker_dispatch(self):
        pumps = []
        dispatcher = CallbackDispatcher(
            pump_factory=lambda callback: pumps.append(_DeferredPump(callback)) or pumps[-1],
        )
        session = _make_chat_session(dispatcher)
        received = []

        def complete_in_worker(
            _message,
            _persona,
            request_id,
            _streaming,
            _images,
            _history,
            _allow_tools,
        ):
            session._dispatch_callback(
                session._on_chat_ready,
                request_id,
                "done",
                None,
            )

        session._run_stream_chat = complete_in_worker

        class ThreadHub:
            def submit_io(self, callback, *args, **kwargs):
                worker = threading.Thread(target=callback, args=args, kwargs=kwargs)
                worker.start()
                worker.join(timeout=2)
                return object()

        with patch("lib.script.chat.ollama_session.get_compute_hub", return_value=ThreadHub()):
            session.stream_chat("hello", "persona", received.append)

        self.assertTrue(session.is_chat_busy)
        self.assertEqual(received, [])
        pumps[0].flush()
        self.assertFalse(session.is_chat_busy)
        self.assertEqual(received, ["done"])
        dispatcher.cleanup()

    def test_rejected_chat_submission_clears_busy_and_completes_request(self):
        dispatcher = CallbackDispatcher(pump_factory=_DeferredPump)
        session = _make_chat_session(dispatcher)
        received = []

        class RejectingHub:
            def submit_io(self, _callback, *_args, **_kwargs):
                return None

        with patch("lib.script.chat.ollama_session.get_compute_hub", return_value=RejectingHub()):
            session.stream_chat("hello", "persona", received.append)

        self.assertFalse(session.is_chat_busy)
        self.assertEqual(
            received,
            ["当前回复模式请求失败: 后台任务调度不可用"],
        )
        dispatcher.cleanup()

    def test_qt_pump_delivers_multiple_worker_callbacks_to_owner_thread(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            r"""
            import json
            import threading

            from PyQt5.QtCore import QCoreApplication, QTimer

            from lib.core.event.callbacks import CallbackDispatcher

            owner_thread_id = threading.get_ident()
            dispatcher = CallbackDispatcher()
            app = QCoreApplication.instance() or QCoreApplication([])
            received = []
            first_dispatched = threading.Event()
            release_first = threading.Event()

            def record(label):
                received.append((label, threading.get_ident()))

            def first_worker():
                dispatcher.dispatch(record, "worker-a")
                first_dispatched.set()
                release_first.wait(2)

            def second_worker():
                dispatcher.dispatch(record, "worker-b")

            thread_a = threading.Thread(target=first_worker)
            thread_a.start()
            if not first_dispatched.wait(2):
                raise RuntimeError("first worker did not dispatch")

            thread_b = threading.Thread(target=second_worker)
            thread_b.start()
            thread_b.join(timeout=2)

            QTimer.singleShot(200, app.quit)
            app.exec_()
            release_first.set()
            thread_a.join(timeout=2)

            print(json.dumps({
                "owner": owner_thread_id,
                "received": received,
            }))
            dispatcher.cleanup()
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        output_lines = [line for line in result.stdout.splitlines() if line.strip()]
        payload = json.loads(output_lines[-1])
        self.assertEqual(
            [item[0] for item in payload["received"]],
            ["worker-a", "worker-b"],
        )
        self.assertTrue(
            all(item[1] == payload["owner"] for item in payload["received"])
        )


if __name__ == "__main__":
    unittest.main()
