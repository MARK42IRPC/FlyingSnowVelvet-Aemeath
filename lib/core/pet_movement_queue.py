"""主宠物移动队列管理器 - 事件驱动移动步骤调度。"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Callable, Optional

from PyQt5.QtCore import QPoint

from lib.core.event.center import get_event_center, EventType, Event


@dataclass
class MoveStep:
    """单个移动步骤。"""

    event_id: str
    source: str
    step_type: str
    x: int
    y: int
    radius: int = 12
    timeout_ms: int = 0
    created_at_ms: float = 0.0
    started_at_ms: float | None = None

    @property
    def target(self) -> QPoint:
        return QPoint(int(self.x), int(self.y))


class PetMoveQueueManager:
    """事件驱动的主宠物移动步骤队列。"""

    def __init__(
        self,
        *,
        on_step_activated: Callable[[MoveStep], None],
        on_step_updated: Callable[[MoveStep], None],
        on_step_cancelled: Callable[[], None],
        on_queue_idle: Callable[[], None],
    ) -> None:
        self._event_center = get_event_center()
        self._on_step_activated = on_step_activated
        self._on_step_updated = on_step_updated
        self._on_step_cancelled = on_step_cancelled
        self._on_queue_idle = on_queue_idle

        self._queue: list[MoveStep] = []
        self._current: MoveStep | None = None

        self._event_center.subscribe(EventType.PET_MOVE_ENQUEUE, self._on_enqueue)
        self._event_center.subscribe(EventType.PET_MOVE_PASS, self._on_pass)
        self._event_center.subscribe(EventType.TICK, self._on_tick)

    @property
    def current_step(self) -> MoveStep | None:
        return self._current

    def cleanup(self) -> None:
        self._event_center.unsubscribe(EventType.PET_MOVE_ENQUEUE, self._on_enqueue)
        self._event_center.unsubscribe(EventType.PET_MOVE_PASS, self._on_pass)
        self._event_center.unsubscribe(EventType.TICK, self._on_tick)
        self._queue.clear()
        self._current = None

    def clear_all(self, result: str = "cancelled") -> None:
        """清空当前步骤与队列。"""
        self._queue.clear()
        if self._current is not None:
            step = self._current
            self._current = None
            self._on_step_cancelled()
            self._publish_done(step, result)
            self._on_queue_idle()

    def handle_movement_complete(self) -> bool:
        """由 MovementController 完成回调驱动。返回是否已激活下一步。"""
        if self._current is None:
            self._on_queue_idle()
            return False

        step = self._current
        self._current = None
        self._publish_done(step, "done")
        return self._activate_next()

    def _on_enqueue(self, event: Event) -> None:
        step = self._build_step(event.data or {})
        if step is None:
            return

        current = self._current
        if current is not None and current.event_id == step.event_id:
            self._current = replace(
                step,
                created_at_ms=current.created_at_ms,
                started_at_ms=current.started_at_ms,
            )
            self._on_step_updated(self._current)
            event.mark_handled()
            return

        for idx, queued in enumerate(self._queue):
            if queued.event_id == step.event_id:
                self._queue[idx] = replace(
                    step,
                    created_at_ms=queued.created_at_ms,
                    started_at_ms=None,
                )
                event.mark_handled()
                return

        insert_mode = str((event.data or {}).get("insert_mode") or "append").strip().lower()
        if insert_mode == "prepend":
            self._queue.insert(0, step)
        elif insert_mode == "replace_queue":
            self._queue = [step]
        elif insert_mode == "replace_all":
            self._queue = [step]
            if self._current is not None:
                cancelled = self._current
                self._current = None
                self._on_step_cancelled()
                self._publish_done(cancelled, "cancelled")
        else:
            self._queue.append(step)

        if self._current is None:
            self._activate_next()
        event.mark_handled()

    def _on_pass(self, event: Event) -> None:
        data = event.data or {}
        scope = str(data.get("scope") or "current").strip().lower()
        source = str(data.get("source") or "").strip()
        step_type = str(data.get("type") or "").strip()
        event_id = str(data.get("event_id") or "").strip()
        result = str(data.get("result") or "cancelled").strip().lower() or "cancelled"

        self._queue = [
            step for step in self._queue
            if not self._matches(step, scope, source, step_type, event_id, include_queue=True)
        ]

        cancelled_current = None
        if self._current is not None and self._matches(
            self._current,
            scope,
            source,
            step_type,
            event_id,
            include_queue=False,
        ):
            cancelled_current = self._current
            self._current = None
            self._on_step_cancelled()
            self._publish_done(cancelled_current, result)

        if cancelled_current is not None:
            if not self._activate_next():
                self._on_queue_idle()

        event.mark_handled()

    def _on_tick(self, event: Event) -> None:
        current = self._current
        if current is None:
            self._activate_next()
            return

        if current.timeout_ms <= 0 or current.started_at_ms is None:
            return

        now_ms = time.monotonic() * 1000.0
        if now_ms - current.started_at_ms < current.timeout_ms:
            return

        timed_out = current
        self._current = None
        self._on_step_cancelled()
        self._publish_done(timed_out, "timeout")
        if not self._activate_next():
            self._on_queue_idle()

    def _activate_next(self) -> bool:
        if self._current is not None or not self._queue:
            return self._current is not None

        step = self._queue.pop(0)
        step.started_at_ms = time.monotonic() * 1000.0
        self._current = step
        self._on_step_activated(step)
        return True

    def _build_step(self, data: dict) -> Optional[MoveStep]:
        pos = data.get("position")
        if isinstance(pos, QPoint):
            tx, ty = pos.x(), pos.y()
        elif isinstance(pos, (list, tuple)) and len(pos) >= 2:
            tx, ty = pos[0], pos[1]
        else:
            tx = data.get("x")
            ty = data.get("y")

        try:
            x = int(round(float(tx)))
            y = int(round(float(ty)))
        except (TypeError, ValueError):
            return None

        try:
            radius = max(1, int(data.get("radius", 12)))
        except (TypeError, ValueError):
            radius = 12

        try:
            timeout_ms = max(0, int(data.get("timeout_ms", 0)))
        except (TypeError, ValueError):
            timeout_ms = 0

        source = str(data.get("source") or "unknown").strip() or "unknown"
        step_type = str(data.get("type") or "move").strip() or "move"
        event_id = str(data.get("event_id") or f"{source}:{step_type}:{int(time.time() * 1000)}").strip()

        return MoveStep(
            event_id=event_id,
            source=source,
            step_type=step_type,
            x=x,
            y=y,
            radius=radius,
            timeout_ms=timeout_ms,
            created_at_ms=time.monotonic() * 1000.0,
        )

    @staticmethod
    def _matches(
        step: MoveStep,
        scope: str,
        source: str,
        step_type: str,
        event_id: str,
        *,
        include_queue: bool,
    ) -> bool:
        if scope == "all":
            return True
        if scope == "current":
            return not include_queue
        if scope == "source":
            return bool(source) and step.source == source
        if scope == "type":
            return bool(step_type) and step.step_type == step_type
        if scope == "event_id":
            return bool(event_id) and step.event_id == event_id
        return False

    def _publish_done(self, step: MoveStep, result: str) -> None:
        self._event_center.publish(Event(EventType.PET_MOVE_DONE, {
            "event_id": step.event_id,
            "source": step.source,
            "type": step.step_type,
            "position": QPoint(step.x, step.y),
            "radius": step.radius,
            "timeout_ms": step.timeout_ms,
            "result": result,
        }))
