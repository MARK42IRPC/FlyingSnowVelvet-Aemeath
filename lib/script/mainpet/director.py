"""主宠行为导演：决定「什么时候请求什么动作」与「往哪里走」。

与状态机的分工：
- 本模块只做决策，通过 `request_action` 回调请求动作切换；
- 状态机负责该请求是否被接受（迁移矩阵）。

搬迁自原 `mainpet/state.py` 的漫游目标选择、沙发保护半径聚合与
游戏区避让，逻辑保持不变。
"""
from __future__ import annotations

import math
import random
from typing import Callable

from config.actions import actions_in_group
from config.config import BEHAVIOR
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.game_obstacles import get_game_obstacle_rect
from lib.core.logger import get_logger
from lib.core.render.visuals.collision import (
    adjust_rect,
    rects_intersect,
    segment_intersects_rect,
)
from lib.core.render.visuals.types import Point, Rect, coerce_point
from lib.core.screen_utils import get_screen_rect_for_point, get_virtual_screen_rect


_logger = get_logger(__name__)

_MOVE_MAX_DURATION_MS = 5000
_MOVE_SOURCE = "mainpet_state"
_MOVE_TRACK_LEOPARD_ID = "mainpet_track_leopard"
_MOVE_TRACK_LEOPARD_TYPE = "track_leopard"
_MOVE_LOCK_SOFA_ID = "mainpet_lock_sofa"
_MOVE_LOCK_SOFA_TYPE = "lock_sofa"
_MOVE_NEAR_SPEAKER_ID = "mainpet_near_speaker"
_MOVE_NEAR_SPEAKER_TYPE = "near_speaker"
_MOVE_WANDER_RANDOM_ID = "mainpet_wander_random"
_MOVE_WANDER_RANDOM_TYPE = "wander_random"


def log(msg):
    """导演日志默认静默，需要排查时可临时恢复。"""
    # _logger.debug("[BehaviorDirector] %s", msg)


class BehaviorDirector:
    """自动行为与漫游决策。"""

    def __init__(
        self,
        *,
        entity,
        request_action: Callable[[str], None],
        on_wander_pause: Callable[[], None],
        on_wander_resume: Callable[[], None],
        defer=None,
    ):
        self._entity = entity
        self._request_action = request_action
        self._on_wander_pause = on_wander_pause
        self._on_wander_resume = on_wander_resume
        self._defer = defer
        self._event_center = get_event_center()

        self._behavior_task_id = None
        self._wander_task_id = None

        # 是否正在动态追踪雪豹
        self._is_tracking_leopard = False
        # 是否因保护半径暂停漫游
        self._paused_by_sofa = False
        # 保护半径检测请求聚合
        self._protection_check_seq = 0
        self._pending_protection_request_id = None
        self._pending_protection_any = False

        self._event_center.subscribe(EventType.PROTECTION_RESPONSE, self._on_protection_response)
        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.TIMER, self._on_timer)
        self._event_center.subscribe(EventType.MOUSE_PRESS, self._on_mouse_press)

    # ── 生命周期 ──────────────────────────────────────────────────────

    @property
    def wander_task_id(self):
        return self._wander_task_id

    @property
    def behavior_task_id(self):
        return self._behavior_task_id

    def start(self, timing_manager) -> None:
        """注册周期任务。由状态机在持有计时器时调用。"""
        self._timing_manager = timing_manager
        self._behavior_task_id = timing_manager.add_task(
            random.randint(*BEHAVIOR["auto_behavior_interval"]), repeat=True
        )
        self._wander_task_id = timing_manager.add_task(
            random.randint(*BEHAVIOR["auto_wander_interval"]), repeat=True
        )

    # ── 状态机回调 ────────────────────────────────────────────────────

    def on_enter_base(self, action_id: str) -> None:
        """进入 base 层：恢复漫游计时器（保护半径内除外）。"""
        if action_id != "idle":
            return
        if self._paused_by_sofa:
            return
        self._on_wander_resume()

    def on_leave_base(self) -> None:
        """离开 base 层：暂停漫游计时器。"""
        self._on_wander_pause()

    # ── 周期任务 ──────────────────────────────────────────────────────

    def _on_timer(self, event) -> None:
        task_id = event.data.get("task_id")
        if task_id == self._behavior_task_id:
            self._trigger_behavior()
        elif task_id == self._wander_task_id:
            self._trigger_wander()

    def _on_mouse_press(self, event) -> None:
        """左键点击触发随机互动动作（移动中被点击时已由输入层切回 base）。"""
        from lib.core.input.types import MouseButton

        if event.data.get("button") != MouseButton.LEFT:
            return
        if self._entity.is_moving():
            return
        self.force_random_action()

    def _trigger_behavior(self) -> None:
        """自动行为：请求回到 idle，由状态机从 idle 池按权重抽取具体动作。"""
        if self._entity.is_moving():
            return
        self._request_action("idle")

    def force_random_action(self) -> None:
        """移动超时等异常情况下的兜底：随便播一个互动动作。"""
        candidates = actions_in_group("click")
        if candidates:
            self._request_action(random.choice(candidates))

    def _on_tick(self, _event) -> None:
        """每 TICK：刷新雪豹追踪目标 + 检测保护半径。"""
        if self._is_tracking_leopard:
            if not self._entity.is_moving():
                self._is_tracking_leopard = False
            else:
                latest = self._get_nearest_leopard_pos()
                if latest is not None:
                    self._enqueue_move(
                        latest,
                        event_id=_MOVE_TRACK_LEOPARD_ID,
                        move_type=_MOVE_TRACK_LEOPARD_TYPE,
                        radius=self._default_move_radius(),
                    )
                else:
                    self._is_tracking_leopard = False
                    log("追踪目标雪豹已消失，解除动态追踪")

        self._check_sofa_protection()

    # ── 漫游 ──────────────────────────────────────────────────────────

    def _trigger_wander(self) -> None:
        """按优先级选漫游目标：雪豹 > 沙发 > 音响附近 > 随机点。"""
        if self._paused_by_sofa:
            return
        if self._entity.is_moving():
            return

        target = self._get_nearest_leopard_pos()
        if target is not None:
            self._is_tracking_leopard = True
            self._enqueue_move(
                target,
                event_id=_MOVE_TRACK_LEOPARD_ID,
                move_type=_MOVE_TRACK_LEOPARD_TYPE,
                radius=self._default_move_radius(),
            )
            return

        self._is_tracking_leopard = False

        target = self._get_nearest_sofa_pos()
        if target is not None:
            self._enqueue_move(
                target,
                event_id=_MOVE_LOCK_SOFA_ID,
                move_type=_MOVE_LOCK_SOFA_TYPE,
                radius=self._default_move_radius(),
            )
            return

        target = self._get_random_pos_near_speaker()
        if target is not None:
            self._enqueue_move(
                target,
                event_id=_MOVE_NEAR_SPEAKER_ID,
                move_type=_MOVE_NEAR_SPEAKER_TYPE,
                radius=self._default_move_radius(),
            )
            return

        screen = get_virtual_screen_rect()
        min_x = int(screen.x + 100)
        max_x = int(screen.x + screen.width - 200)
        min_y = int(screen.y + 100)
        max_y = int(screen.y + screen.height - 200)
        if max_x < min_x:
            min_x = max_x = int(screen.x)
        if max_y < min_y:
            min_y = max_y = int(screen.y)
        target = Point(random.randint(min_x, max_x), random.randint(min_y, max_y))
        if self._is_wander_target_blocked_by_lahai(target):
            log(f"Wander target skipped by game area filter: ({target.x}, {target.y})")
            return
        self._enqueue_move(
            target,
            event_id=_MOVE_WANDER_RANDOM_ID,
            move_type=_MOVE_WANDER_RANDOM_TYPE,
            radius=self._default_move_radius(),
        )

    def _get_nearest_leopard_pos(self) -> Point | None:
        from lib.script.plugin_registry import get_manager

        manager = get_manager("snow_leopard")
        if manager is None:
            return None
        nearest = coerce_point(manager.get_nearest_leopard_pos(self._entity.get_core_position()))
        if nearest is None:
            return None
        pet_geom = self._entity.get_core_geometry()
        if pet_geom is None:
            return None
        return Point(nearest.x - pet_geom.width // 2, nearest.y - pet_geom.height // 2)

    def _get_nearest_sofa_pos(self) -> Point | None:
        from lib.script.plugin_registry import get_manager

        manager = get_manager("sofa")
        if manager is None:
            return None
        nearest = coerce_point(manager.get_nearest_sofa_pos(self._entity.get_core_position()))
        if nearest is None:
            return None
        pet_geom = self._entity.get_core_geometry()
        if pet_geom is None:
            return None
        return Point(nearest.x - pet_geom.width // 2, nearest.y - pet_geom.height // 2)

    def _get_random_pos_near_speaker(self) -> Point | None:
        from lib.script.plugin_registry import get_manager

        manager = get_manager("speaker")
        if manager is None:
            return None
        speaker_points = manager.get_speaker_centers()
        if not speaker_points:
            return None

        pet_pos = self._entity.get_core_position()
        speaker_center = min(
            speaker_points,
            key=lambda p: (p.x - pet_pos.x) ** 2 + (p.y - pet_pos.y) ** 2,
        )

        try:
            radius = max(1, int(BEHAVIOR.get("wander_near_speaker_radius", 300)))
        except (TypeError, ValueError):
            radius = 300

        angle = random.uniform(0.0, 2.0 * math.pi)
        distance = int(radius * (random.random() ** 0.5))
        target_center_x = speaker_center.x + int(math.cos(angle) * distance)
        target_center_y = speaker_center.y + int(math.sin(angle) * distance)

        pet_geom = self._entity.get_core_geometry()
        if pet_geom is None:
            return None

        screen_geom = get_screen_rect_for_point(speaker_center)
        target_x = target_center_x - pet_geom.width // 2
        target_y = target_center_y - pet_geom.height // 2

        min_x, min_y = screen_geom.x, screen_geom.y
        max_x = screen_geom.x + screen_geom.width - pet_geom.width
        max_y = screen_geom.y + screen_geom.height - pet_geom.height
        if max_x < min_x:
            max_x = min_x
        if max_y < min_y:
            max_y = min_y
        return Point(
            max(min_x, min(target_x, max_x)),
            max(min_y, min(target_y, max_y)),
        )

    # ── 游戏区避让 ────────────────────────────────────────────────────

    def _is_wander_target_blocked_by_lahai(self, target: Point) -> bool:
        """目标矩形或其移动线段是否遮挡游戏区域。"""
        pet_geom = self._entity.get_core_geometry()
        if pet_geom is None:
            return False
        try:
            blocked_rect = get_game_obstacle_rect()
        except Exception:
            return False
        if not isinstance(blocked_rect, Rect) or blocked_rect.width <= 0 or blocked_rect.height <= 0:
            return False

        target_rect = Rect(target.x, target.y, pet_geom.width, pet_geom.height)
        if rects_intersect(target_rect, blocked_rect):
            return True

        current_pos = self._entity.get_core_position()
        current_center = Point(
            current_pos.x + pet_geom.width // 2, current_pos.y + pet_geom.height // 2
        )
        target_center = Point(
            target.x + pet_geom.width // 2, target.y + pet_geom.height // 2
        )
        expanded_blocked = adjust_rect(
            blocked_rect,
            -int(pet_geom.width) // 2,
            -int(pet_geom.height) // 2,
            int(pet_geom.width) // 2,
            int(pet_geom.height) // 2,
        )
        return segment_intersects_rect(current_center, target_center, expanded_blocked)

    # ── 保护半径 ──────────────────────────────────────────────────────

    def _check_sofa_protection(self) -> None:
        """每 tick 聚合一次保护半径检测，统一更新保护状态。"""
        pet_pos = self._entity.get_core_position()
        pet_geom = self._entity.get_core_geometry()
        pet_cx = pet_pos.x + pet_geom.width // 2
        pet_cy = pet_pos.y + pet_geom.height // 2

        self._protection_check_seq += 1
        request_id = f"protection-{self._protection_check_seq}"
        self._pending_protection_request_id = request_id
        self._pending_protection_any = False

        self._event_center.publish(Event(EventType.PROTECTION_CHECK, {
            "pet_position": Point(pet_cx, pet_cy),
            "current_in_protection": self._paused_by_sofa,
            "request_id": request_id,
        }))
        self._defer_call(0, lambda rid=request_id: self._finalize_protection_check(rid))

    def _defer_call(self, delay_ms: int, callback) -> None:
        if self._defer is not None:
            self._defer(delay_ms, callback)
            return
        from lib.core.render.registry import get_deferred_call

        deferred_call = get_deferred_call()
        if deferred_call is not None:
            deferred_call(delay_ms, callback)
        else:
            callback()

    def _on_protection_response(self, event: Event) -> None:
        request_id = event.data.get("request_id")
        if not request_id or request_id != self._pending_protection_request_id:
            return
        self._pending_protection_any = self._pending_protection_any or bool(
            event.data.get("in_protection", False)
        )

    def _finalize_protection_check(self, request_id: str) -> None:
        if request_id != self._pending_protection_request_id:
            return

        in_protection = bool(self._pending_protection_any)
        self._pending_protection_request_id = None
        self._pending_protection_any = False

        if in_protection and not self._paused_by_sofa:
            self._paused_by_sofa = True
            if self._entity.is_moving():
                self._pass_move(scope="current")
            self._on_wander_pause()
            log("主宠物进入保护半径，漫游计时器已暂停")
        elif not in_protection and self._paused_by_sofa:
            self._paused_by_sofa = False
            self._on_wander_resume()
            log("主宠物离开保护半径，漫游计时器已恢复")

    # ── 移动队列 ──────────────────────────────────────────────────────

    @staticmethod
    def _default_move_radius() -> int:
        try:
            return max(1, int(BEHAVIOR.get("move_arrival_radius", 12)))
        except (TypeError, ValueError):
            return 12

    def _enqueue_move(self, target: Point, *, event_id: str, move_type: str, radius: int) -> None:
        self._event_center.publish(Event(EventType.PET_MOVE_ENQUEUE, {
            "event_id": event_id,
            "source": _MOVE_SOURCE,
            "type": move_type,
            "position": Point(round(target.x), round(target.y)),
            "radius": radius,
            "timeout_ms": _MOVE_MAX_DURATION_MS,
        }))

    def _pass_move(self, *, scope: str = "current", move_type: str | None = None) -> None:
        payload = {"scope": scope, "source": _MOVE_SOURCE, "result": "cancelled"}
        if move_type:
            payload["type"] = move_type
        self._event_center.publish(Event(EventType.PET_MOVE_PASS, payload))
