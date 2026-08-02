from lib.core.graphics.anchors import get_anchor_point
from lib.core.graphics.types import Point, Rect, coerce_point
from lib.core.movement_controller import MovementController
from lib.core.pet_movement_queue import MoveStep, PetMoveQueueManager


class _QtLikePoint:
    def x(self):
        return 12

    def y(self):
        return 34


def test_coerce_point_accepts_core_and_legacy_point_shapes():
    assert coerce_point(Point(1, 2)) == Point(1, 2)
    assert coerce_point((3, 4)) == Point(3, 4)
    assert coerce_point(_QtLikePoint()) == Point(12, 34)
    assert coerce_point("invalid") is None


def test_core_anchor_calculation_is_backend_neutral():
    rect = Rect(10, 20, 100, 40)
    assert get_anchor_point(rect, "top") == Point(60, 20)
    assert get_anchor_point(rect, "bottom_right") == Point(110, 60)
    assert get_anchor_point(rect, "unknown") == Point(60, 40)


def test_movement_controller_uses_backend_neutral_points():
    updates = []
    controller = MovementController(on_position_update=updates.append)
    controller.sync_position(Point(0, 0))
    controller.start_move(Point(20, 0))
    controller.update_tick()
    rendered = controller.update_frame(1.0)

    assert isinstance(rendered, Point)
    assert updates[-1] == rendered
    assert controller.target == Point(20, 0)


def test_move_step_exposes_backend_neutral_target():
    step = MoveStep("id", "test", "move", 10, 20)
    assert step.target == Point(10, 20)


def test_move_queue_accepts_legacy_point_like_payload():
    manager = PetMoveQueueManager(
        on_step_activated=lambda _step: None,
        on_step_updated=lambda _step: None,
        on_step_cancelled=lambda: None,
        on_queue_idle=lambda: None,
    )
    try:
        step = manager._build_step({"position": _QtLikePoint()})
        assert step is not None
        assert step.target == Point(12, 34)
    finally:
        manager.cleanup()
