"""Backend-neutral movement orchestration for the main desktop pet."""
from __future__ import annotations

from collections.abc import Callable

from lib.core.event.center import Event, EventType
from lib.core.graphics.types import Point, coerce_point
from lib.core.movement_controller import MovementController, MovementSettings
from lib.core.pet_movement_queue import MoveStep, PetMoveQueueManager


class PetMovementRuntime:
    """Coordinate movement interpolation, queueing, dragging, and state requests."""

    def __init__(
        self,
        *,
        event_center,
        get_position: Callable[[], Point],
        on_position_update: Callable[[Point], None],
        get_state: Callable[[], str],
        request_state: Callable[[str, bool], None],
        on_direction_change: Callable[[bool], None] | None = None,
        movement_settings: MovementSettings | None = None,
    ) -> None:
        self._event_center = event_center
        self._get_position = get_position
        self._on_position_update = on_position_update
        self._get_state = get_state
        self._request_state = request_state
        self._external_direction_change = on_direction_change
        self._legacy_move_event_id = "pet_window_api_move"
        self._user_dragging = False
        self._cleaned = False

        self._controller = MovementController(
            on_position_update=self._handle_position_update,
            on_move_complete=self._handle_movement_complete,
            on_direction_change=self._handle_direction_change,
            settings=movement_settings,
        )
        self._queue = PetMoveQueueManager(
            on_step_activated=self._activate_step,
            on_step_updated=self._update_step,
            on_step_cancelled=self._cancel_active_step,
            on_queue_idle=self._handle_queue_idle,
            can_accept_step=self._can_accept_step,
            event_center=event_center,
        )

    @property
    def is_moving(self) -> bool:
        return self._controller.is_moving

    @property
    def target(self) -> Point:
        return self._controller.target

    @property
    def flipped(self) -> bool:
        return self._controller.flipped

    @flipped.setter
    def flipped(self, value: bool) -> None:
        self._controller.flipped = bool(value)

    @property
    def is_user_dragging(self) -> bool:
        return self._user_dragging

    def start_move(self, target: Point | object) -> None:
        self._publish_move_request(target)

    def update_move_target(self, target: Point | object) -> None:
        self._publish_move_request(target)

    def stop_move(self) -> None:
        self._event_center.publish(Event(EventType.PET_MOVE_PASS, {
            "scope": "current",
            "result": "cancelled",
        }))

    def begin_user_drag(self) -> Point | None:
        if self._user_dragging:
            return None
        self._user_dragging = True
        self._queue.clear_all(result="cancelled")
        current_pos = self._current_position()
        self._controller.sync_position(current_pos)
        return current_pos

    def update_user_drag_position(self, new_pos: Point | object) -> Point | None:
        point = coerce_point(new_pos)
        if point is None:
            return None
        self._on_position_update(point)
        self._controller.sync_position(point)
        return point

    def end_user_drag(self) -> Point | None:
        if not self._user_dragging:
            return None
        self._user_dragging = False
        current_pos = self._current_position()
        self._controller.sync_position(current_pos)
        return current_pos

    def update_frame(self, alpha: float) -> Point | None:
        if not self._controller.is_moving:
            return None
        return self._controller.update_frame(alpha)

    def update_tick(self) -> None:
        if self._controller.is_moving:
            self._controller.update_tick()

    def teleport(self, position: Point | object) -> Point | None:
        point = coerce_point(position)
        if point is None:
            return None
        self._queue.clear_all(result="cancelled")
        self._on_position_update(point)
        self._controller.sync_position(point)
        return point

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self._queue.cleanup()

    def _publish_move_request(self, target: Point | object) -> None:
        point = coerce_point(target)
        if point is None:
            raise TypeError(f"cannot convert movement target to Point: {target!r}")
        self._event_center.publish(Event(EventType.PET_MOVE_ENQUEUE, {
            "event_id": self._legacy_move_event_id,
            "source": "pet_window_api",
            "type": "move",
            "position": point,
            "radius": 12,
            "timeout_ms": 0,
        }))

    def _current_position(self) -> Point:
        return coerce_point(self._get_position()) or Point()

    def _activate_step(self, step: MoveStep) -> None:
        if self._user_dragging:
            return
        self._controller.sync_position(self._current_position())
        self._controller.start_move(step.target, arrival_radius=step.radius)
        if self._get_state() != "moving":
            self._request_state("moving", False)

    def _update_step(self, step: MoveStep) -> None:
        if self._user_dragging:
            return
        if not self._controller.is_moving:
            self._activate_step(step)
            return
        self._controller.update_target(step.target, arrival_radius=step.radius)

    def _cancel_active_step(self) -> None:
        self._controller.sync_position(self._current_position())
        self._controller.stop_move()

    def _can_accept_step(self) -> bool:
        return not self._user_dragging

    def _handle_queue_idle(self) -> None:
        if self._get_state() == "moving":
            self._request_state("idle", False)

    def _handle_position_update(self, position: Point) -> None:
        self._on_position_update(position)

    def _handle_movement_complete(self) -> None:
        if self._queue.handle_movement_complete():
            return
        if self._get_state() == "moving":
            self._request_state("idle", True)

    def _handle_direction_change(self, flipped: bool) -> None:
        if self._external_direction_change is not None:
            self._external_direction_change(flipped)
