import unittest
from pathlib import Path

from lib.core.event.center import EventType
from lib.core.game_obstacles import (
    configure_game_obstacle_provider,
    reset_game_obstacle_provider,
)
from lib.core.graphics.types import Point, Rect
from lib.script.mainpet.state import StateMachine


class _Entity:
    def __init__(self, position=Point(0, 0), geometry=Rect(0, 0, 20, 20)):
        self.position = position
        self.geometry = geometry

    def get_core_position(self):
        return self.position

    def get_core_geometry(self):
        return self.geometry

    def is_moving(self):
        return False


class _EventSink:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class MainPetStateGeometryTests(unittest.TestCase):
    def tearDown(self):
        reset_game_obstacle_provider()

    def test_state_module_has_no_explicit_pyqt_dependency(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "lib"
            / "script"
            / "mainpet"
            / "state.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("PyQt5", source)
        self.assertNotIn("lib.script.gemes", source)

    def test_lahai_filter_uses_core_rect_and_segment_collision(self):
        state = StateMachine.__new__(StateMachine)
        state._entity = _Entity(position=Point(0, 40), geometry=Rect(0, 0, 20, 20))
        configure_game_obstacle_provider(lambda: Rect(40, 40, 20, 40))

        self.assertTrue(state._is_wander_target_blocked_by_lahai(Point(80, 40)))
        self.assertFalse(state._is_wander_target_blocked_by_lahai(Point(0, 100)))

    def test_lahai_filter_is_inactive_without_a_desktop_game_host(self):
        state = StateMachine.__new__(StateMachine)
        state._entity = _Entity(position=Point(0, 40), geometry=Rect(0, 0, 20, 20))

        self.assertFalse(state._is_wander_target_blocked_by_lahai(Point(80, 40)))

    def test_protection_request_and_move_queue_payloads_use_core_points(self):
        state = StateMachine.__new__(StateMachine)
        state._entity = _Entity(position=Point(10, 20), geometry=Rect(0, 0, 40, 60))
        state._event_center = _EventSink()
        state._paused_by_sofa = False
        state._protection_check_seq = 0
        state._pending_protection_request_id = None
        state._pending_protection_any = False
        deferred = []
        state._defer = lambda delay, callback: deferred.append((delay, callback))

        state._check_sofa_protection()
        state._enqueue_move(
            Point(12.4, 33.6),
            event_id="move",
            move_type="wander",
            radius=12,
            timeout_ms=5000,
        )

        protection = state._event_center.events[0]
        movement = state._event_center.events[1]
        self.assertIs(protection.type, EventType.PROTECTION_CHECK)
        self.assertEqual(protection.data["pet_position"], Point(30, 50))
        self.assertEqual(deferred[0][0], 0)
        self.assertIs(movement.type, EventType.PET_MOVE_ENQUEUE)
        self.assertEqual(movement.data["position"], Point(12, 34))


if __name__ == "__main__":
    unittest.main()
