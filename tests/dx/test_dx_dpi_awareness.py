from __future__ import annotations

import ctypes
import unittest

from lib.core.dx_bridge.dpi_awareness import (
    ensure_per_monitor_v2_dpi_awareness,
)


class _Function:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


def _value(context) -> int:
    return int(context.value if isinstance(context, ctypes.c_void_p) else context or 0)


class _User32:
    def __init__(self, *, current: int, process_succeeds: bool, thread_succeeds: bool):
        self.current = current
        self.process_succeeds = process_succeeds
        self.thread_succeeds = thread_succeeds
        self.process_calls = 0
        self.thread_calls = 0
        self.GetThreadDpiAwarenessContext = _Function(lambda: self.current)
        self.AreDpiAwarenessContextsEqual = _Function(
            lambda left, right: _value(left) == _value(right)
        )
        self.GetAwarenessFromDpiAwarenessContext = _Function(
            lambda context: 2 if _value(context) in {_value(ctypes.c_void_p(-3)), _value(ctypes.c_void_p(-4))} else 0
        )
        self.SetProcessDpiAwarenessContext = _Function(self._set_process)
        self.SetThreadDpiAwarenessContext = _Function(self._set_thread)

    def _set_process(self, target):
        self.process_calls += 1
        if self.process_succeeds:
            self.current = _value(target)
            return True
        return False

    def _set_thread(self, target):
        self.thread_calls += 1
        previous = self.current
        if self.thread_succeeds:
            self.current = _value(target)
            return previous
        return 0


class DxDpiAwarenessTests(unittest.TestCase):
    def test_existing_pmv2_context_is_kept(self):
        api = _User32(current=_value(ctypes.c_void_p(-4)), process_succeeds=False, thread_succeeds=False)
        self.assertTrue(ensure_per_monitor_v2_dpi_awareness(api))
        self.assertEqual(api.process_calls, 0)
        self.assertEqual(api.thread_calls, 0)

    def test_process_is_promoted_before_windows_are_created(self):
        api = _User32(current=1, process_succeeds=True, thread_succeeds=False)
        self.assertTrue(ensure_per_monitor_v2_dpi_awareness(api))
        self.assertEqual(api.process_calls, 1)
        self.assertEqual(api.thread_calls, 0)

    def test_locked_process_uses_owner_thread_context(self):
        api = _User32(current=1, process_succeeds=False, thread_succeeds=True)
        self.assertTrue(ensure_per_monitor_v2_dpi_awareness(api))
        self.assertEqual(api.process_calls, 1)
        self.assertEqual(api.thread_calls, 1)

    def test_unaware_context_failure_is_reported(self):
        api = _User32(current=1, process_succeeds=False, thread_succeeds=False)
        self.assertFalse(ensure_per_monitor_v2_dpi_awareness(api))


if __name__ == "__main__":
    unittest.main()
