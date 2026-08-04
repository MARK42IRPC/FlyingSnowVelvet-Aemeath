import unittest

from lib.core.layer_manager import LayerManager


class _Widget:
    def isVisible(self) -> bool:
        return True


class LayerManagerSchedulerTests(unittest.TestCase):
    def test_frame_enforcement_flushes_registered_window_once(self):
        manager = LayerManager()
        enforced = []
        manager._enforce_all = lambda: enforced.append(True)

        manager.enforce_on_frame()
        self.assertEqual(enforced, [])

        widget = _Widget()
        manager.register(widget)
        manager.enforce_on_frame()
        manager.enforce_on_frame()

        self.assertEqual(enforced, [True])

        manager.set_layer(widget, "PANEL")
        manager.enforce_on_frame()
        manager.enforce_on_frame()

        self.assertEqual(enforced, [True, True])

        manager.unregister(widget)
        manager.enforce_on_frame()
        manager.enforce_on_frame()

        self.assertEqual(enforced, [True, True, True])

    def test_paused_frame_enforcement_keeps_pending_layer_change(self):
        manager = LayerManager()
        enforced = []
        manager._enforce_all = lambda: enforced.append(True)
        widget = _Widget()

        manager.register(widget)
        manager.pause()
        manager.enforce_on_frame()

        self.assertEqual(enforced, [])

        manager.resume()
        manager.enforce_on_frame()

        self.assertEqual(enforced, [True])

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
