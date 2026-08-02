import unittest

from lib.core.layer_manager import LayerManager


class LayerManagerSchedulerTests(unittest.TestCase):
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
