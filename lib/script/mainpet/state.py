"""状态机模块 - 管理宠物动画状态与行为。"""
import math
import random

from config.config import BEHAVIOR
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.action import Actions
from lib.core.logger import get_logger
from lib.core.graphics.collision import (
    adjust_rect,
    rects_intersect,
    segment_intersects_rect,
)
from lib.core.graphics.types import Point, Rect, coerce_point
from lib.core.input.types import MouseButton
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
    """统一状态机日志输出。"""
    _logger.debug("[StateMachine] %s", msg)


class StateMachine:
    """宠物状态机（基于事件驱动）。"""

    def __init__(self, entity, timing_manager=None, *, defer=None):
        """
        初始化状态机。

        Args:
            entity: BaseEntity 实例（PetWindow）
            timing_manager: 计时器管理器
        """
        self._entity = entity
        self._current_state = 'idle'
        self._timing_manager = timing_manager
        self._defer = defer
        self._event_center = get_event_center()

        # 当前动作已播放循环次数
        self._current_loop_count = 0

        # 是否正在动态追踪雪豹（漫游目标为活动雪豹时为 True）
        self._is_tracking_leopard = False

        # 是否因保护半径而暂停漫游计时器
        self._paused_by_sofa = False

        # 保护半径检测请求聚合状态
        self._protection_check_seq = 0
        self._pending_protection_request_id = None
        self._pending_protection_any = False

        # 订阅保护半径检测响应事件
        self._event_center.subscribe(EventType.PROTECTION_RESPONSE, self._on_protection_response)
        self._event_center.subscribe(EventType.PET_MOVE_DONE, self._on_pet_move_done)

        # 订阅相关事件
        self._event_center.subscribe(EventType.TIMER, self._on_timer_event)
        self._event_center.subscribe(EventType.GIF_LOOP_COMPLETED, self._on_gif_loop_completed)
        self._event_center.subscribe(EventType.MOUSE_PRESS, self._on_mouse_press)
        self._event_center.subscribe(EventType.STATE_CHANGE_REQUEST, self._on_state_change_request)
        self._event_center.subscribe(EventType.TICK, self._on_tick_tracking)

        log("StateMachine initialized")

        # 启动定时任务
        if self._timing_manager:
            self._schedule_behavior()
            self._schedule_wander()

    def _on_timer_event(self, event):
        """处理计时器事件。"""
        task_id = event.data.get('task_id')
        if task_id == self._behavior_task_id:
            self._trigger_behavior()
        elif task_id == self._wander_task_id:
            log(f"Timer event: wander triggered")
            self._trigger_wander()

    def _on_gif_loop_completed(self, event):
        """处理 GIF 循环完成事件。"""
        state = event.data.get('state')

        # 只处理当前状态对应的回调
        if state != self._current_state:
            return

        self._current_loop_count += 1

        # 获取当前动作定义
        current_action = Actions.get_action(self._current_state)
        if not current_action:
            return

        # 检查是否达到播放次数
        if current_action.repeat_count > 0 and self._current_loop_count >= current_action.repeat_count:
            log("Reached repeat count, switching to idle")
            # 发布回到 idle 的请求
            self._publish_state_change_request('idle', by_event=False)

    def _on_mouse_press(self, event):
        """处理鼠标按下事件。"""
        button = event.data.get('button')
        was_moving = bool(event.data.get('was_moving', False))

        # 该点击发生时若处于移动中，已在输入层完成 stop_move 并切回 idle；
        # 这里直接触发随机 action，形成“点击打断 move 并进入随机动作”。
        if was_moving:
            action = Actions.get_random_action_from_group("action1")
            if action:
                self._publish_state_change_request(action.name, by_event=False)
            return

        if button == MouseButton.LEFT:
            # 左键点击：随机触发 action1 组动作
            if not self._entity.is_moving():
                action = Actions.get_random_action_from_group("action1")

                if action:
                    # 发布状态切换请求
                    self._publish_state_change_request(action.name, by_event=False)

    def _on_state_change_request(self, event):
        """处理状态切换请求事件。"""
        new_state = event.data.get('new_state')
        by_event = event.data.get('by_event', True)

        if new_state:
            self._process_state_change(new_state, by_event)

    def _publish_state_change_request(self, new_state: str, by_event: bool = True):
        """发布状态切换请求事件。"""
        event = Event(EventType.STATE_CHANGE_REQUEST, {
            'new_state': new_state,
            'by_event': by_event
        })
        self._event_center.publish(event)

    def _process_state_change(self, new_state: str, by_event: bool = True):
        """处理状态切换逻辑。"""
        log(f"Process state change: {self._current_state} -> {new_state}, by_event={by_event}")

        # 获取新动作定义
        new_action = Actions.get_action(new_state)
        if not new_action:
            log(f"Action not found: {new_state}")
            return

        # 获取当前动作定义
        current_action = Actions.get_action(self._current_state)

        # 特殊规则：
        # 1) moving 只允许从 idle 进入
        # 2) idle 可被随时切回
        # 3) 非 idle -> 非 idle 需要当前动作可打断
        if new_state == 'moving' and self._current_state != 'idle':
            log("Moving can only interrupt idle")
            return
        
        if self._current_state != 'idle' and new_state != 'idle':
            if current_action and not Actions.is_interruptible(current_action, by_event):
                log("Cannot interrupt current action")
                return

        # 切换状态
        self._current_state = new_state

        # 处理漫游计时器暂停/恢复
        if new_state == 'idle':
            # 回到 idle 时恢复计时器；若当前处于保护态则跳过
            if self._timing_manager and not self._paused_by_sofa:
                log("Resuming wander timer")
                self._timing_manager.resume_task(self._wander_task_id)
        elif new_state == 'moving':
            # 进入 moving 时暂停计时器
            if self._timing_manager:
                log("Pausing wander timer (moving)")
                self._timing_manager.pause_task(self._wander_task_id)
        elif not Actions.is_stay_action(new_action):
            # 进入普通动作时暂停计时器
            if self._timing_manager:
                log(f"Pausing wander timer (action: {new_state})")
                self._timing_manager.pause_task(self._wander_task_id)

        # 发布动作开始事件（非 stay 动作）
        if not Actions.is_stay_action(new_action):
            self._publish_action_event(EventType.ACTION_START, new_state)
            self._current_loop_count = 0

            # 如需在动作开始时触发粒子效果，立即发布
            if new_action.has_particle_effect() and new_action.particle_config.get('trigger') == 'start':
                self._spawn_particle_effect(new_action.particle_config)

        # 通知实体切换状态
        self._entity.change_state(new_state)
        log(f"State changed to: {self._current_state}")

    def _publish_action_event(self, event_type: EventType, action_name: str):
        """发布动作事件。"""
        event = Event(event_type, {
            'action_name': action_name,
            'loop_count': self._current_loop_count
        })
        self._event_center.publish(event)

    def _spawn_particle_effect(self, particle_config: dict):
        """
        触发粒子效果。

        Args:
            particle_config: 粒子配置
        """
        particle_id = particle_config.get('particle_id', 'scatter_fall')
        area_type = particle_config.get('area_type', 'point')

        # 获取实体位置作为粒子生成位置
        pos = self._entity.get_core_position()
        rect = self._entity.get_core_geometry()

        # 鏍规嵁鍖哄煙绫诲瀷鐢熸垚绮掑瓙
        if area_type == 'point':
            # 使用实体中心点
            center_x = pos.x + rect.width // 2
            center_y = pos.y + rect.height // 2
            area_data = (center_x, center_y)
        elif area_type == 'rect':
            # 使用实体边界
            area_data = (pos.x, pos.y, pos.x + rect.width, pos.y + rect.height)
        elif area_type == 'circle':
            # 使用实体中心点和半径
            center_x = pos.x + rect.width // 2
            center_y = pos.y + rect.height // 2
            radius = min(rect.width, rect.height) // 2
            area_data = (center_x, center_y, radius)
        else:
            return

        # 发布粒子请求事件
        particle_event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': particle_id,
            'area_type': area_type,
            'area_data': area_data
        })
        self._event_center.publish(particle_event)

    def get_current_state(self) -> str:
        """获取当前状态。"""
        return self._current_state

    # ==================================================================
    # 琛屼负璋冨害
    # ==================================================================

    def _schedule_behavior(self):
        lo, hi = BEHAVIOR['auto_behavior_interval']
        interval = random.randint(lo, hi)
        self._behavior_task_id = self._timing_manager.add_task(
            interval,
            repeat=True
        )

    def _trigger_behavior(self):
        """触发自动行为切换。"""
        # 仅在非 moving 状态下触发
        if not self._entity.is_moving() and self._current_state != 'moving':
            state = random.choice(BEHAVIOR['random_states'])
            # 非事件打断，确保动作完整播放
            self._publish_state_change_request(state, by_event=False)

    def _schedule_wander(self):
        lo, hi = BEHAVIOR['auto_wander_interval']
        interval = random.randint(lo, hi)
        log(f"Scheduling wander timer: interval={interval}ms")
        self._wander_task_id = self._timing_manager.add_task(
            interval,
            repeat=True
        )

    def _trigger_wander(self):
        """Trigger wander movement.

        Priority:
        1) active snow leopard (dynamic tracking)
        2) alive sofa
        3) random point near speaker
        4) fully random screen point
        """
        log(f"Wander triggered, current_state={self._current_state}, is_moving={self._entity.is_moving()}")

        # Sofa protection has higher priority than all wander target selection.
        if self._paused_by_sofa:
            log("Wander skipped: in sofa protection")
            return

        if not self._entity.is_moving():
            # 1) nearest active snow leopard
            target = self._get_nearest_leopard_pos()
            if target is not None:
                self._is_tracking_leopard = True
                log(f"Leopard tracking target: ({target.x}, {target.y})")
                self._enqueue_move(
                    target,
                    event_id=_MOVE_TRACK_LEOPARD_ID,
                    move_type=_MOVE_TRACK_LEOPARD_TYPE,
                    radius=self._default_move_radius(),
                    timeout_ms=_MOVE_MAX_DURATION_MS,
                )
            else:
                self._is_tracking_leopard = False
                # 2) nearest sofa
                target = self._get_nearest_sofa_pos()
                if target is not None:
                    log(f"Sofa target: ({target.x}, {target.y})")
                    self._enqueue_move(
                        target,
                        event_id=_MOVE_LOCK_SOFA_ID,
                        move_type=_MOVE_LOCK_SOFA_TYPE,
                        radius=self._default_move_radius(),
                        timeout_ms=_MOVE_MAX_DURATION_MS,
                    )
                else:
                    # 3) random point around speaker
                    target = self._get_random_pos_near_speaker()
                    if target is not None:
                        log(f"Speaker-near target: ({target.x}, {target.y})")
                        self._enqueue_move(
                            target,
                            event_id=_MOVE_NEAR_SPEAKER_ID,
                            move_type=_MOVE_NEAR_SPEAKER_TYPE,
                            radius=self._default_move_radius(),
                            timeout_ms=_MOVE_MAX_DURATION_MS,
                        )
                    else:
                        # 4) random point on screen
                        screen = get_virtual_screen_rect()
                        min_x = int(screen.x + 100)
                        max_x = int(screen.x + screen.width - 200)
                        min_y = int(screen.y + 100)
                        max_y = int(screen.y + screen.height - 200)
                        if max_x < min_x:
                            min_x = max_x = int(screen.x)
                        if max_y < min_y:
                            min_y = max_y = int(screen.y)
                        target = Point(
                            random.randint(min_x, max_x),
                            random.randint(min_y, max_y),
                        )
                        if self._is_wander_target_blocked_by_lahai(target):
                            log(
                                f"Wander target skipped by Lahai game area filter: "
                                f"({target.x}, {target.y})"
                            )
                            return
                        self._enqueue_move(
                            target,
                            event_id=_MOVE_WANDER_RANDOM_ID,
                            move_type=_MOVE_WANDER_RANDOM_TYPE,
                            radius=self._default_move_radius(),
                            timeout_ms=_MOVE_MAX_DURATION_MS,
                        )

    def _on_tick_tracking(self, event):
        """
        每个 TICK（50ms）执行：
        1. 刷新雪豹动态追踪目标（仅在追踪中）
        2. 检测保护半径并统一更新漫游计时器状态
        """
        # 雪豹动态追踪
        if self._is_tracking_leopard:
            if not self._entity.is_moving():
                # 已到达目标或被中断，解除追踪
                self._is_tracking_leopard = False
            else:
                latest = self._get_nearest_leopard_pos()
                if latest is not None:
                    self._enqueue_move(
                        latest,
                        event_id=_MOVE_TRACK_LEOPARD_ID,
                        move_type=_MOVE_TRACK_LEOPARD_TYPE,
                        radius=self._default_move_radius(),
                        timeout_ms=_MOVE_MAX_DURATION_MS,
                    )
                else:
                    # 追踪目标消失（如淡出），解除追踪
                    self._is_tracking_leopard = False
                    log("追踪目标雪豹已消失，解除动态追踪")

        # 保护半径检测（始终执行）
        self._check_sofa_protection()

    def _on_pet_move_done(self, event: Event) -> None:
        """处理主宠物移动队列完成事件。"""
        if event.data.get('source') != _MOVE_SOURCE:
            return

        move_type = event.data.get('type')
        result = str(event.data.get('result') or '').strip().lower()
        if move_type == _MOVE_TRACK_LEOPARD_TYPE:
            self._is_tracking_leopard = False

        if result != 'timeout':
            return

        self._is_tracking_leopard = False
        log("Move timeout from queue manager, forcing random action")
        action = Actions.get_random_action_from_group("action1")
        if action:
            self._publish_state_change_request(action.name, by_event=False)

    def _get_nearest_leopard_pos(self) -> Point | None:
        """
        查询最近活动雪豹中心坐标，并转换为宠物窗口左上角目标坐标。
        管理器不存在或无活动雪豹时返回 None。
        """
        from lib.core.plugin_registry import get_manager
        
        manager = get_manager('snow_leopard')
        if manager is None:
            return None
        
        pet_pos = self._entity.get_core_position()
        nearest = manager.get_nearest_leopard_pos(pet_pos)
        nearest = coerce_point(nearest)
        if nearest is None:
            return None
        
        # 将目标中心转换为宠物左上角移动目标
        pet_geom = self._entity.get_core_geometry()
        if pet_geom is None:
            return None
        
        return Point(
            nearest.x - pet_geom.width // 2,
            nearest.y - pet_geom.height // 2,
        )

    def _get_nearest_sofa_pos(self) -> Point | None:
        """
        查询最近存活沙发中心坐标，并转换为宠物窗口左上角目标坐标。
        管理器不存在或无存活沙发时返回 None。
        """
        from lib.core.plugin_registry import get_manager
        
        manager = get_manager('sofa')
        if manager is None:
            return None
        
        pet_pos = self._entity.get_core_position()
        nearest = manager.get_nearest_sofa_pos(pet_pos)
        nearest = coerce_point(nearest)
        if nearest is None:
            return None
        
        # 将目标中心转换为宠物左上角移动目标
        pet_geom = self._entity.get_core_geometry()
        if pet_geom is None:
            return None
        
        return Point(
            nearest.x - pet_geom.width // 2,
            nearest.y - pet_geom.height // 2,
        )

    def _get_random_pos_near_speaker(self) -> Point | None:
        """
        If speakers exist, return a random target near the nearest speaker.
        Returns the pet window top-left target position; None when unavailable.
        """
        from lib.core.plugin_registry import get_manager

        manager = get_manager('speaker')
        if manager is None:
            return None

        speaker_points = manager.get_speaker_centers()
        if not speaker_points:
            return None

        pet_pos = self._entity.get_core_position()
        nearest = min(
            speaker_points,
            key=lambda point: (
                (point.x - pet_pos.x) ** 2
                + (point.y - pet_pos.y) ** 2
            )
        )
        speaker_center = nearest

        raw_radius = BEHAVIOR.get('wander_near_speaker_radius', 300)
        try:
            radius = max(1, int(raw_radius))
        except (TypeError, ValueError):
            radius = 300

        # Uniform sample in a disk.
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

        min_x = screen_geom.x
        min_y = screen_geom.y
        max_x = screen_geom.x + screen_geom.width - pet_geom.width
        max_y = screen_geom.y + screen_geom.height - pet_geom.height
        if max_x < min_x:
            max_x = min_x
        if max_y < min_y:
            max_y = min_y
        target_x = max(min_x, min(target_x, max_x))
        target_y = max(min_y, min(target_y, max_y))

        return Point(target_x, target_y)

    def _check_sofa_protection(self):
        """
        每个 tick 发起一次保护半径检测请求。
        将多个管理器响应聚合后，再统一更新保护状态，避免同一 tick 内抖动。
        """
        # 计算宠物中心坐标
        pet_pos = self._entity.get_core_position()
        pet_geom = self._entity.get_core_geometry()
        pet_cx = pet_pos.x + pet_geom.width // 2
        pet_cy = pet_pos.y + pet_geom.height // 2

        self._protection_check_seq += 1
        request_id = f"protection-{self._protection_check_seq}"
        self._pending_protection_request_id = request_id
        self._pending_protection_any = False

        # 发布保护半径检测请求（携带 request_id 用于聚合）
        self._event_center.publish(Event(EventType.PROTECTION_CHECK, {
            'pet_position': Point(pet_cx, pet_cy),
            'current_in_protection': self._paused_by_sofa,
            'request_id': request_id,
        }))
        self._defer_call(0, lambda rid=request_id: self._finalize_protection_check(rid))

    def _defer_call(self, delay_ms: int, callback) -> None:
        if self._defer is not None:
            self._defer(delay_ms, callback)
            return
        from lib.core.desktop_backend import get_deferred_call

        deferred_call = get_deferred_call()
        if deferred_call is not None:
            deferred_call(delay_ms, callback)
        else:
            callback()

    def _on_protection_response(self, event: Event):
        """处理保护半径检测响应（先聚合，不立即切状态）。"""
        request_id = event.data.get('request_id')
        if not request_id or request_id != self._pending_protection_request_id:
            return
        self._pending_protection_any = self._pending_protection_any or bool(
            event.data.get('in_protection', False)
        )

    def _finalize_protection_check(self, request_id: str):
        """在当前请求的响应处理后，统一应用保护状态。"""
        if request_id != self._pending_protection_request_id:
            return

        in_protection = bool(self._pending_protection_any)
        self._pending_protection_request_id = None
        self._pending_protection_any = False

        if in_protection and not self._paused_by_sofa:
            self._paused_by_sofa = True
            if self._entity.is_moving():
                self._pass_move(scope='current')
            if self._timing_manager:
                self._timing_manager.pause_task(self._wander_task_id)
            log("主宠物进入保护半径，漫游计时器已暂停")
        elif not in_protection and self._paused_by_sofa:
            self._paused_by_sofa = False
            if self._timing_manager:
                self._timing_manager.resume_task(self._wander_task_id)
            log("主宠物离开保护半径，漫游计时器已恢复")

    def _is_wander_target_blocked_by_lahai(self, target: Point) -> bool:
        """
        当默认漫游目标会遮挡拉海洛方块游戏区域的横向中间三分之一时，返回 True。

        拦截两种情况：
        1. 目标矩形本身会压到游戏区
        2. 从当前位置到目标位置的移动线段会横跨游戏区
        """
        try:
            from lib.script.gemes import get_game_runtime
        except Exception:
            return False

        pet_geom = self._entity.get_core_geometry()
        if pet_geom is None:
            return False

        try:
            blocked_rect = get_game_runtime().get_lahai_game_middle_third_rect_global()
        except Exception:
            return False

        if not isinstance(blocked_rect, Rect) or blocked_rect.width <= 0 or blocked_rect.height <= 0:
            return False

        target_rect = Rect(
            target.x,
            target.y,
            pet_geom.width,
            pet_geom.height,
        )
        if rects_intersect(target_rect, blocked_rect):
            return True

        current_pos = self._entity.get_core_position()
        current_center = Point(
            current_pos.x + pet_geom.width // 2,
            current_pos.y + pet_geom.height // 2,
        )
        target_center = Point(
            target.x + pet_geom.width // 2,
            target.y + pet_geom.height // 2,
        )

        # 将游戏危险区按桌宠半尺寸向外扩一圈。
        # 这样即使仅移动轨迹中心线擦过，桌宠实体本身也不会横跨遮挡到下落区。
        expanded_blocked = adjust_rect(
            blocked_rect,
            -int(pet_geom.width) // 2,
            -int(pet_geom.height) // 2,
            int(pet_geom.width) // 2,
            int(pet_geom.height) // 2,
        )
        return segment_intersects_rect(current_center, target_center, expanded_blocked)

    def _default_move_radius(self) -> int:
        raw_value = BEHAVIOR.get('move_arrival_radius', 12)
        try:
            return max(1, int(raw_value))
        except (TypeError, ValueError):
            return 12

    def _enqueue_move(
        self,
        target: Point,
        *,
        event_id: str,
        move_type: str,
        radius: int,
        timeout_ms: int,
    ) -> None:
        self._event_center.publish(Event(EventType.PET_MOVE_ENQUEUE, {
            'event_id': event_id,
            'source': _MOVE_SOURCE,
            'type': move_type,
            'position': Point(round(target.x), round(target.y)),
            'radius': radius,
            'timeout_ms': timeout_ms,
        }))

    def _pass_move(self, *, scope: str = 'current', move_type: str | None = None) -> None:
        payload = {
            'scope': scope,
            'source': _MOVE_SOURCE,
            'result': 'cancelled',
        }
        if move_type:
            payload['type'] = move_type
        self._event_center.publish(Event(EventType.PET_MOVE_PASS, payload))



