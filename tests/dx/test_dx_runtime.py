import subprocess
import sys
import textwrap
import threading
import unittest
from pathlib import Path

from lib.core.dx_bridge.application_runtime import DxApplicationRuntime
from lib.core.dx_bridge.event_pump import DxEventPump
from lib.core.dx_bridge.loop import DxLoopContext
from lib.core.dx_bridge.scheduler import DxScheduler


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance_ms(self, milliseconds: int) -> None:
        self.value += milliseconds / 1000.0


class DxSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.context = DxLoopContext(clock=self.clock)
        self.scheduler = DxScheduler(self.context)

    def tearDown(self):
        self.scheduler.cleanup()

    def test_periodic_timer_start_stop_restart_and_interval_change(self):
        callbacks = []
        timer = self.scheduler.create_periodic_timer(
            lambda: callbacks.append(self.clock.value)
        )

        timer.start(10)
        self.clock.advance_ms(9)
        self.context.run_once()
        self.assertEqual(callbacks, [])
        self.clock.advance_ms(1)
        self.context.run_once()
        self.assertEqual(len(callbacks), 1)
        self.assertTrue(timer.active)
        self.assertEqual(timer.interval_ms, 10)

        self.clock.advance_ms(5)
        timer.set_interval(20)
        self.clock.advance_ms(19)
        self.context.run_once()
        self.assertEqual(len(callbacks), 1)
        self.clock.advance_ms(1)
        self.context.run_once()
        self.assertEqual(len(callbacks), 2)

        timer.stop()
        self.clock.advance_ms(100)
        self.context.run_once()
        self.assertEqual(len(callbacks), 2)
        self.assertFalse(timer.active)

        timer.start(1)
        self.clock.advance_ms(1)
        self.context.run_once()
        self.assertEqual(len(callbacks), 3)

    def test_late_periodic_callback_is_coalesced(self):
        callbacks = []
        timer = self.scheduler.create_periodic_timer(lambda: callbacks.append(True))
        timer.start(10)

        self.clock.advance_ms(1000)
        self.context.run_once()
        self.context.run_once()

        self.assertEqual(callbacks, [True])

    def test_cleanup_is_idempotent_and_cancels_delivery(self):
        callbacks = []
        timer = self.scheduler.create_periodic_timer(lambda: callbacks.append(True))
        timer.start(1)

        self.scheduler.cleanup()
        self.scheduler.cleanup()
        self.clock.advance_ms(10)
        self.context.run_once()

        self.assertEqual(callbacks, [])
        self.assertFalse(timer.active)
        with self.assertRaises(RuntimeError):
            timer.start(1)
        with self.assertRaises(RuntimeError):
            self.scheduler.create_periodic_timer(lambda: None)


class DxEventPumpTests(unittest.TestCase):
    def test_worker_emits_coalesce_and_deliver_on_owner_thread(self):
        context = DxLoopContext()
        owner_thread_id = threading.get_ident()
        callback_threads = []
        pump = DxEventPump(
            context,
            lambda: callback_threads.append(threading.get_ident()),
        )

        worker = threading.Thread(target=lambda: [pump.emit() for _ in range(10)])
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(callback_threads, [])

        context.run_once()
        self.assertEqual(callback_threads, [owner_thread_id])

    def test_disconnect_cancels_pending_and_future_delivery(self):
        context = DxLoopContext()
        callbacks = []
        pump = DxEventPump(context, lambda: callbacks.append(True))

        pump.emit()
        pump.disconnect()
        pump.disconnect()
        context.run_once()
        pump.emit()
        context.run_once()

        self.assertEqual(callbacks, [])

    def test_worker_emit_wakes_waiting_owner_loop(self):
        context = DxLoopContext()
        delivered = []
        pump = DxEventPump(context, lambda: delivered.append(True))
        worker = threading.Timer(0.01, pump.emit)
        worker.start()
        try:
            context.run_once(500)
        finally:
            worker.join(timeout=2)

        self.assertEqual(delivered, [True])

    def test_callback_error_is_reported_without_stopping_ready_delivery(self):
        errors = []
        context = DxLoopContext(exception_handler=errors.append)
        delivered = []

        def fail():
            raise ValueError("callback failed")

        context.post(fail)
        context.post(lambda: delivered.append(True))
        context.run_once()

        self.assertEqual(delivered, [True])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)


class DxApplicationRuntimeTests(unittest.TestCase):
    def test_one_shot_exit_and_acknowledgement_run_once(self):
        runtime = DxApplicationRuntime(native_poll_interval_ms=1)
        app = runtime.create_application(object(), ["flying-snow"])
        acknowledgements = []
        callbacks = []
        runtime.connect_exit_acknowledged(app, lambda: acknowledgements.append(True))
        runtime.schedule_once(0, lambda: callbacks.append(threading.get_ident()))
        runtime.schedule_once(0, lambda: runtime.request_exit(app, 7))

        exit_code = runtime.run_event_loop(app)
        second_exit_code = runtime.run_event_loop(app)

        self.assertEqual(exit_code, 7)
        self.assertEqual(second_exit_code, 7)
        self.assertEqual(callbacks, [runtime.context.owner_thread_id])
        self.assertEqual(acknowledgements, [True])

    def test_default_argv_matches_process_arguments(self):
        runtime = DxApplicationRuntime()
        app = runtime.create_application(object())

        self.assertEqual(app.argv, tuple(sys.argv))

    def test_registered_window_poller_is_driven_by_loop(self):
        runtime = DxApplicationRuntime(native_poll_interval_ms=1)
        app = runtime.create_application(object(), [])

        class Poller:
            def __init__(self):
                self.calls = 0

            def is_alive(self):
                return True

            def poll_events(self):
                self.calls += 1
                runtime.request_exit(app, 3)
                return ("close",)

        poller = Poller()
        runtime.register_window_host(poller)

        self.assertEqual(runtime.run_event_loop(app), 3)
        self.assertEqual(poller.calls, 1)

    def test_close_all_windows_attempts_every_registered_host(self):
        runtime = DxApplicationRuntime()
        app = runtime.create_application(object(), [])

        class Host:
            def __init__(self, error=None):
                self.closed = False
                self.error = error

            def poll_events(self):
                return ()

            def close(self):
                self.closed = True
                if self.error is not None:
                    raise self.error

        first = Host(RuntimeError("close failed"))
        second = Host()
        runtime.register_window_host(first)
        runtime.register_window_host(second)

        with self.assertRaisesRegex(RuntimeError, "close failed"):
            runtime.close_all_windows(app)

        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        self.assertEqual(runtime.context.registered_pollers(), ())

    def test_owner_only_loop_rejects_worker_processing(self):
        context = DxLoopContext()
        errors = []

        def process_on_worker():
            try:
                context.run_once()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=process_on_worker)
        worker.start()
        worker.join(timeout=2)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)

    def test_dx_runtime_imports_and_runs_with_pyqt_blocked(self):
        repo_root = Path(__file__).resolve().parents[2]
        script = textwrap.dedent(
            """
            import builtins

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.core.dx_bridge import (
                DxApplicationRuntime,
                DxEventPump,
                DxScheduler,
            )

            runtime = DxApplicationRuntime(native_poll_interval_ms=1)
            app = runtime.create_application(object(), [])
            scheduler = DxScheduler(runtime.context)
            delivered = []
            pump = DxEventPump(runtime.context, lambda: delivered.append(True))
            runtime.schedule_once(0, pump.emit)
            runtime.schedule_once(2, lambda: runtime.request_exit(app, 0))
            assert runtime.run_event_loop(app) == 0
            assert delivered == [True]
            pump.disconnect()
            scheduler.cleanup()
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
