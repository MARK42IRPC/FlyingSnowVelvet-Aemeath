import unittest

from lib.core.layer import Layer
from lib.core.layer_manager import LayerManager


class _Window:
    def __init__(self, handle: int = 1) -> None:
        self.handle = handle
        self.alive = True
        self.visible = True
        self.native_stacking = True
        self.stack_calls = []
        self.raise_calls = 0


class _Host:
    def __init__(self, window: _Window) -> None:
        self.window = window

    @property
    def identity(self) -> int:
        return id(self.window)

    def is_alive(self) -> bool:
        return self.window.alive

    def is_visible(self) -> bool:
        return self.window.visible

    def raise_window(self) -> None:
        self.window.raise_calls += 1

    def stack_window(self, insert_after: int | None) -> int | None:
        self.window.stack_calls.append((self.window.handle, insert_after))
        return self.window.handle if self.window.native_stacking else None


def _host_factory(window: object) -> _Host:
    if not isinstance(window, _Window):
        raise TypeError("expected _Window")
    return _Host(window)


class LayerManagerSchedulerTests(unittest.TestCase):
    def test_frame_enforcement_flushes_registered_window_once(self):
        manager = LayerManager()
        enforced = []
        manager._enforce_all = lambda: enforced.append(True)

        manager.enforce_on_frame()
        self.assertEqual(enforced, [])

        window = _Window()
        manager.register(window)
        manager.enforce_on_frame()
        manager.enforce_on_frame()

        self.assertEqual(enforced, [True])

        manager.set_layer(window, "PANEL")
        manager.enforce_on_frame()
        manager.enforce_on_frame()

        self.assertEqual(enforced, [True, True])

        manager.unregister(window)
        manager.enforce_on_frame()
        manager.enforce_on_frame()

        self.assertEqual(enforced, [True, True, True])

    def test_paused_frame_enforcement_keeps_pending_layer_change(self):
        manager = LayerManager()
        enforced = []
        manager._enforce_all = lambda: enforced.append(True)
        window = _Window()

        manager.register(window)
        manager.pause()
        manager.enforce_on_frame()

        self.assertEqual(enforced, [])

        manager.resume()
        manager.enforce_on_frame()

        self.assertEqual(enforced, [True])

    def test_window_hosts_build_native_stack_chain_from_high_to_low(self):
        manager = LayerManager(host_factory=_host_factory)
        lower = _Window(101)
        higher = _Window(202)

        manager.register(lower, Layer.PANEL)
        manager.register(higher, Layer.DIALOG)
        manager.enforce_now()

        self.assertEqual(higher.stack_calls, [(202, None)])
        self.assertEqual(lower.stack_calls, [(101, 202)])
        self.assertEqual((lower.raise_calls, higher.raise_calls), (0, 0))

    def test_failed_native_stack_falls_back_to_ordered_raise(self):
        manager = LayerManager(host_factory=_host_factory)
        lower = _Window(101)
        higher = _Window(202)
        higher.native_stacking = False

        manager.register(lower, Layer.PANEL)
        manager.register(higher, Layer.DIALOG)
        manager.enforce_now()

        self.assertEqual(higher.stack_calls, [(202, None)])
        self.assertEqual(lower.stack_calls, [])
        self.assertEqual((lower.raise_calls, higher.raise_calls), (1, 1))

    def test_dead_hosts_are_pruned_and_hidden_hosts_are_not_stacked(self):
        manager = LayerManager(host_factory=_host_factory)
        dead = _Window(101)
        hidden = _Window(202)
        visible = _Window(303)

        manager.register(dead, Layer.PANEL, name="dead")
        manager.register(hidden, Layer.PANEL, name="hidden")
        manager.register(visible, Layer.PANEL, name="visible")
        dead.alive = False
        hidden.visible = False
        manager.enforce_now()

        self.assertEqual(dead.stack_calls, [])
        self.assertEqual(hidden.stack_calls, [])
        self.assertEqual(visible.stack_calls, [(303, None)])
        self.assertEqual(
            [row[3] for row in manager.snapshot()],
            ["hidden", "visible"],
        )

    def test_enforce_burst_uses_injected_one_shot_scheduler(self):
        deferred = []
        manager = LayerManager(
            defer=lambda delay_ms, callback: deferred.append((delay_ms, callback)),
        )
        enforced = []
        manager._enforce_all = lambda: enforced.append(True)

        manager.enforce_burst((0, 16, 48))

        self.assertEqual(len(enforced), 1)
        self.assertEqual([delay for delay, _ in deferred], [16, 48])
        for _, callback in deferred:
            callback()
        self.assertEqual(len(enforced), 3)

    def test_paused_layer_manager_does_not_schedule_burst(self):
        deferred = []
        manager = LayerManager(
            defer=lambda delay_ms, callback: deferred.append((delay_ms, callback)),
        )
        manager.pause()

        manager.enforce_burst()

        self.assertEqual(deferred, [])


if __name__ == "__main__":
    unittest.main()
