import unittest
from unittest.mock import patch

from lib.core.event.center import EventCenter
from lib.core.timing.manager import TimingManager


class TimingFrameLimitTests(unittest.TestCase):
    def _manager(self, fps=120):
        event_center = EventCenter()
        patcher = patch(
            "lib.core.timing.manager.get_event_center",
            return_value=event_center,
        )
        self.addCleanup(patcher.stop)
        patcher.start()
        manager = TimingManager(frame_fps=fps)
        self.addCleanup(manager.stop)
        return manager

    def test_limits_compose_and_restore_configured_frame_rate(self):
        manager = self._manager(120)

        self.assertEqual(manager.get_frame_fps(), 120)
        manager.set_frame_fps_limit("workbench", 30)
        self.assertEqual(manager.get_frame_fps(), 30)
        self.assertEqual(manager._frame_timer.interval(), 33)

        manager.set_frame_fps_limit("power_saver", 20)
        self.assertEqual(manager.get_frame_fps(), 20)
        manager.set_frame_fps_limit("workbench", None)
        self.assertEqual(manager.get_frame_fps(), 20)
        manager.set_frame_fps_limit("power_saver", None)
        self.assertEqual(manager.get_frame_fps(), 120)
        self.assertEqual(manager._frame_timer.interval(), 8)

    def test_base_frame_rate_changes_under_limit_without_losing_limit(self):
        manager = self._manager(120)
        manager.set_frame_fps_limit("workbench", 30)

        manager.set_frame_fps(60)
        self.assertEqual(manager.get_frame_fps(), 30)
        self.assertEqual(manager.get_configured_frame_fps(), 60)
        manager.set_frame_fps_limit("workbench", None)
        self.assertEqual(manager.get_frame_fps(), 60)

    def test_limit_source_must_be_named(self):
        manager = self._manager()
        with self.assertRaises(ValueError):
            manager.set_frame_fps_limit("", 30)


if __name__ == "__main__":
    unittest.main()
