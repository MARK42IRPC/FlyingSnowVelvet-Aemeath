import unittest
from pathlib import Path

from lib.core.event.center import EventType
from lib.core.game_obstacles import (
    configure_game_obstacle_provider,
    reset_game_obstacle_provider,
)
from lib.core.render.visuals.types import Point, Rect
from lib.script.mainpet.director import BehaviorDirector
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


def _make_director(entity: _Entity, *, event_center: _EventSink) -> BehaviorDirector:
    director = BehaviorDirector.__new__(BehaviorDirector)
    director._entity = entity
    director._request_action = lambda action_id: None
    director._on_wander_pause = lambda: None
    director._on_wander_resume = lambda: None
    director._event_center = event_center
    director._paused_by_sofa = False
    director._protection_check_seq = 0
    director._pending_protection_request_id = None
    director._pending_protection_any = False
    director._defer = None
    return director


class MainPetStateGeometryTests(unittest.TestCase):
    def tearDown(self):
        reset_game_obstacle_provider()

    def test_state_module_has_no_explicit_pyqt_dependency(self):
        module_dir = Path(__file__).resolve().parents[1] / "lib" / "script" / "mainpet"
        for name in ("state.py", "director.py"):
            source = (module_dir / name).read_text(encoding="utf-8")
            self.assertNotIn("PyQt5", source)
            self.assertNotIn("lib.script.gemes", source)

    def test_lahai_filter_uses_core_rect_and_segment_collision(self):
        director = _make_director(
            _Entity(position=Point(0, 40), geometry=Rect(0, 0, 20, 20)),
            event_center=_EventSink(),
        )
        configure_game_obstacle_provider(lambda: Rect(40, 40, 20, 40))

        self.assertTrue(director._is_wander_target_blocked_by_lahai(Point(80, 40)))
        self.assertFalse(director._is_wander_target_blocked_by_lahai(Point(0, 100)))

    def test_lahai_filter_is_inactive_without_a_desktop_game_host(self):
        director = _make_director(
            _Entity(position=Point(0, 40), geometry=Rect(0, 0, 20, 20)),
            event_center=_EventSink(),
        )

        self.assertFalse(director._is_wander_target_blocked_by_lahai(Point(80, 40)))

    def test_protection_request_and_move_queue_payloads_use_core_points(self):
        sink = _EventSink()
        director = _make_director(
            _Entity(position=Point(10, 20), geometry=Rect(0, 0, 40, 60)),
            event_center=sink,
        )
        deferred = []
        director._defer = lambda delay, callback: deferred.append((delay, callback))

        director._check_sofa_protection()
        director._enqueue_move(
            Point(12.4, 33.6),
            event_id="move",
            move_type="wander",
            radius=12,
        )

        protection = sink.events[0]
        movement = sink.events[1]
        self.assertIs(protection.type, EventType.PROTECTION_CHECK)
        self.assertEqual(protection.data["pet_position"], Point(30, 50))
        self.assertEqual(deferred[0][0], 0)
        self.assertIs(movement.type, EventType.PET_MOVE_ENQUEUE)
        self.assertEqual(movement.data["position"], Point(12, 34))

    def test_state_machine_exposes_request_and_current(self):
        self.assertTrue(hasattr(StateMachine, "request"))
        self.assertTrue(hasattr(StateMachine, "current"))
        self.assertTrue(hasattr(StateMachine, "on_animation_complete"))


if __name__ == "__main__":
    unittest.main()
