import threading
import time
import unittest

from lib.core.compute_hub import ComputeHub


class ComputeHubLifecycleTests(unittest.TestCase):
    def test_cleanup_cancels_queued_work_and_rejects_new_work(self):
        hub = ComputeHub()
        release = threading.Event()
        started = threading.Event()

        def blocking_task():
            started.set()
            release.wait(2)

        active = hub.submit_io(blocking_task)
        self.assertTrue(started.wait(1))
        queued = [hub.submit_io(blocking_task) for _ in range(32)]

        before = time.monotonic()
        hub.cleanup(timeout=0.01)
        elapsed = time.monotonic() - before
        release.set()
        active.result(timeout=1)

        self.assertLess(elapsed, 0.5)
        self.assertTrue(any(future.cancelled() for future in queued))
        with self.assertRaises(RuntimeError):
            hub.submit_io(lambda: None)

    def test_unknown_executor_is_rejected(self):
        hub = ComputeHub()
        try:
            with self.assertRaises(ValueError):
                hub.submit_latest('slot', lambda: None, executor='cpu')
        finally:
            hub.cleanup(timeout=0)

    def test_interactive_io_starts_when_shared_io_pool_is_saturated(self):
        hub = ComputeHub()
        release = threading.Event()
        interactive_started = threading.Event()

        def blocking_task():
            release.wait(2)

        try:
            blockers = [
                hub.submit_io(blocking_task)
                for _ in range(hub._io_pool._max_workers + 4)
            ]
            interactive = hub.submit_interactive_io(interactive_started.set)
            self.assertTrue(interactive_started.wait(1))
            interactive.result(timeout=1)
        finally:
            release.set()
            for future in blockers:
                try:
                    future.result(timeout=1)
                except Exception:
                    pass
            hub.cleanup(timeout=0)


if __name__ == '__main__':
    unittest.main()
