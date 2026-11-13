import unittest
from unittest.mock import Mock

from lib.core.event.center import Event, EventType
from lib.core.physics import PhysicsBody, PhysicsWorld


def _bare_world(body: PhysicsBody) -> PhysicsWorld:
    world = object.__new__(PhysicsWorld)
    world._paused = False
    world._bodies = [body]
    world._pending_future = None
    return world


class PhysicsIdleTests(unittest.TestCase):
    def test_inactive_body_does_not_emit_position_updates_each_frame(self):
        body = PhysicsBody(10.0, 20.0, 20.0, 50, 50)
        body.prev_x = 8.0
        body.prev_y = 18.0
        body.render_x = 9.0
        body.render_y = 19.0
        body.on_position_change = Mock()
        world = _bare_world(body)

        world._on_frame(Event(EventType.FRAME, {"tick_alpha": 0.5}))

        self.assertEqual((body.render_x, body.render_y), (10.0, 20.0))
        body.on_position_change.assert_not_called()

    def test_active_body_still_emits_interpolated_position_update(self):
        body = PhysicsBody(10.0, 20.0, 20.0, 50, 50)
        body.active = True
        body.prev_x = 6.0
        body.prev_y = 12.0
        body.on_position_change = Mock()
        world = _bare_world(body)

        world._on_frame(Event(EventType.FRAME, {"tick_alpha": 0.5}))

        self.assertEqual((body.render_x, body.render_y), (8.0, 16.0))
        body.on_position_change.assert_called_once_with(body)

    def test_idle_tick_skips_screen_query_and_compute_submission(self):
        body = PhysicsBody(10.0, 20.0, 20.0, 50, 50)
        world = _bare_world(body)
        world._apply_pending_updates = Mock()
        world._refresh_screen_bounds = Mock()
        world._submit_frame_job = Mock()

        world._on_tick(Event(EventType.TICK))

        world._apply_pending_updates.assert_called_once_with()
        world._refresh_screen_bounds.assert_not_called()
        world._submit_frame_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
