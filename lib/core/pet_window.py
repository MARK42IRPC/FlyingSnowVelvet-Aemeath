"""宠物窗口模块"""
from abc import abstractmethod
import math
import random

from config.config import ANIMATION, BEHAVIOR, UI
from lib.core.layer_manager import get_layer_manager
from lib.core.input.click import ClickHandler
from lib.core.input.key import KeyHandler
from lib.core.input.types import KeyboardInput, MouseButton, MouseInput
from lib.core.timing.manager import TimingManager
from lib.core.movement_controller import MovementSettings
from lib.core.pet_movement_runtime import PetMovementRuntime
from lib.core.entity.base import BaseEntity
from lib.core.event.mouse_handler import MouseEventHandler
from lib.core.logger import get_logger

_logger = get_logger(__name__)
from lib.core.voice.ams_startup import AmsStartupSound
from lib.core.event.key_handler import KeyEventHandler
from lib.core.event.center import get_event_center, EventType, Event
from lib.script.mainpet.state import StateMachine
from config.user_scale_config import get_user_scale_config
from lib.core.draw_core import DrawRequest, get_draw_core
from lib.core.layer import Layer, normalize_layer
from lib.core.graphics.types import Point, Size, coerce_point
from lib.core.action import Actions
from lib.core.timing import register_timing_manager
from lib.core.clickthrough_state import set_clickthrough_enabled
from config.scale import scale_px


def _get_main_pet_opacity() -> float:
    raw_value = UI.get('pet_opacity', 1.0)
    try:
        opacity = float(raw_value)
    except (TypeError, ValueError):
        opacity = 1.0
    return max(0.0, min(1.0, opacity))


class PetWindow(BaseEntity):
    """
    与桌面后端无关的主宠控制器。

    具体窗口宿主通过受保护的 ``_host_*`` 方法承接窗口、输入和 UI
    副作用；当前 Qt 组合实现位于 ``qt_bridge.pet_window``。
    """

    @abstractmethod
    def _host_create_scheduler(self):
        """Create the scheduler owned by the concrete desktop host."""

    @abstractmethod
    def _host_cursor_position(self) -> Point:
        """Return the current cursor position in desktop coordinates."""

    @abstractmethod
    def _host_setup(self, on_close) -> None:
        """Initialize the native window and toolkit UI components."""

    @abstractmethod
    def _host_finalize_startup(self) -> None:
        """Show the native window and complete toolkit-side preloading."""

    @abstractmethod
    def _host_publish_anchor_response(self, **kwargs) -> None:
        """Publish a native-window anchor response at the backend boundary."""

    @abstractmethod
    def _host_set_clickthrough(self, enabled: bool) -> None:
        """Apply click-through behavior to the native window."""

    @abstractmethod
    def _host_toggle_command_dialog(self) -> None:
        """Toggle the toolkit command dialog."""

    @abstractmethod
    def _host_move(self, position: Point) -> None:
        """Move the native window."""

    @abstractmethod
    def _host_request_repaint(self) -> None:
        """Request a native-window repaint."""

    @abstractmethod
    def _host_shutdown_ui(self) -> None:
        """Close toolkit UI owned by the pet host."""

    def __init__(self, gifs: dict, particle_overlay: object):
        super().__init__()

        self._gifs    = gifs
        self._particles = particle_overlay
        self._core_cleanup_done = False

        # ── 绘制核心 ───────────────────────────────────────────────────
        self._draw_core = get_draw_core()

        # ── 核心运行状态 ──────────────────────────────────────────────
        self._event_center = get_event_center()
        self._state = 'idle'

        # ── 移动运行时 ──────────────────────────────────────────────────
        self._movement = PetMovementRuntime(
            event_center=self._event_center,
            get_position=self.get_core_position,
            on_position_update=self._on_movement_position_update,
            get_state=lambda: self._state,
            request_state=self._publish_state_change_request,
            on_direction_change=self._on_direction_change,
            movement_settings=MovementSettings(
                min_speed=float(BEHAVIOR['move_min_speed']),
                acceleration=float(BEHAVIOR['move_acceleration']),
                max_speed=float(BEHAVIOR['move_max_speed']),
                decel_distance=float(BEHAVIOR['move_decel_distance']),
            ),
        )

        # 鼠标穿透状态
        self._clickthrough = False
        set_clickthrough_enabled(False)

        # ── 输入处理器 ────────────────────────────────────────────────
        self._click_handler = ClickHandler(
            self,
            cursor_position_provider=self._host_cursor_position,
        )
        self._key_handler = KeyHandler(self)

        # ── 事件处理器 ───────────────────────────────────────────────
        self._mouse_event_handler = MouseEventHandler(self)
        self._key_event_handler = KeyEventHandler(self)
        self._startup_voice_sound = AmsStartupSound(interruptible=False)

        # ── 计时器管理器 ──────────────────────────────────────────────
        self._timing_manager = TimingManager(
            frame_fps=ANIMATION['frame_fps'],
            gif_fps=ANIMATION['gif_fps'],
            scheduler=self._host_create_scheduler(),
        )
        self._timing_manager.start()

        # 注册全局访问器，供子对象（雪豹、雪堆等）使用 add_task
        register_timing_manager(self._timing_manager)

        # 订阅帧事件（用于窗口移动）
        self._event_center.subscribe(EventType.FRAME, self._handle_frame_event)

        # 订阅 TICK 事件（用于速度计算）
        self._event_center.subscribe(EventType.TICK, self._handle_tick_event)

        # 订阅GIF帧事件（用于动画播放）
        self._event_center.subscribe(EventType.GIF_FRAME, self._handle_gif_frame_event)

        # 订阅定时器事件（用于状态切换和延迟任务）
        self._event_center.subscribe(EventType.TIMER, self._handle_timer_event)

        # ── 订阅绘制事件 ───────────────────────────────────────────────
        self._event_center.subscribe(EventType.DRAW_REQUEST, self._handle_draw_request)

        # ── 订阅 UI 事件 ─────────────────────────────────────────────
        self._event_center.subscribe(EventType.UI_CREATE, self._handle_ui_create)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._handle_clickthrough_toggle)

        # ── 订阅实体位置请求事件（支持管理器解耦通信）────────────────
        self._event_center.subscribe(EventType.ENTITY_POSITION_REQUEST, self._handle_entity_position_request)

        # ── 订阅实体状态查询事件（支持管理器解耦通信）────────────────
        self._event_center.subscribe(EventType.ENTITY_STATE_QUERY, self._handle_entity_state_query)
        # ── 订阅主宠物瞬移事件 ───────────────────────────────────────
        self._event_center.subscribe(EventType.PET_TELEPORT, self._handle_pet_teleport)

        # ── 注册所有 GIF 资源到 DrawCore ───────────────────────────────
        self._register_all_resources()

        # ── 状态机 ────────────────────────────────────────────────────
        self._state_machine = StateMachine(self, self._timing_manager)

        # ── 移动任务ID ──────────────────────────────────────────────
        self._move_task_id = None

        # ── 移动粒子：累计位移每 30px 触发一次 flicker_data ─────────────
        self._move_particle_step_px = 30.0
        self._move_particle_distance_accum = 0.0
        self._move_particle_last_pos = None
        self._move_particle_enabled = False

        # 鈹€鈹€ 鍒濆浣嶇疆 / UI / 绐楀彛灞炴€?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        self._host_setup(on_close=self._request_app_quit)


        # ── 定时器 ────────────────────────────────────────────────────
        # 使用 TimingManager 管理移动任务（动画使用GIF_FRAME事件驱动）
        self._task_callbacks = {}  # task_id -> callback 映射
        self._move_task_id = None  # 移动任务需要时才添加

        # 初始状态和绘制
        # 启动时使用随机 action 而不是 idle
        random_action = Actions.get_random_action_from_group("action1")
        if random_action:
            # 通过事件系统触发状态切换，确保状态机正确处理
            event = Event(EventType.STATE_CHANGE_REQUEST, {
                'new_state': random_action.name,
                'by_event': False
            })
            self._event_center.publish(event)
        else:
            self._change_state('idle')
        self._host_finalize_startup()

    def _register_all_resources(self):
        """将所有 GIF 资源注册到 DrawCore"""
        for resource in self._gifs.values():
            self._draw_core.register_resource(resource)

    # ==================================================================
    # 绘制事件处理
    # ==================================================================

    def _handle_draw_request(self, event):
        """
        处理绘制请求事件
        事件数据格式: [资源id, 资源帧[如有], 绘制位置]
        """
        data = event.data

        # 支持两种格式
        # 格式1: {'resource_id': str, 'frame_index': int, 'position': (x, y), ...}
        if isinstance(data, dict):
            resource_id = data.get('resource_id')
            frame_index = data.get('frame_index', -1)
            position = data.get('position')
            alpha = data.get('alpha', 1.0)
            flipped = data.get('flipped', self._movement.flipped)
            scale = data.get('scale', 1.0)
            layer = data.get('layer', Layer.MAIN_PET)
            z = data.get('z', 0)
            clear_others = data.get('clear_others', False)
        # 格式2: [资源id, 资源帧[如有], 绘制位置]
        else:
            resource_id = data[0] if len(data) > 0 else None
            frame_index = data[1] if len(data) > 1 else -1
            position = data[2] if len(data) > 2 else None
            alpha = 1.0
            flipped = self._movement.flipped
            scale = 1.0
            layer = Layer.MAIN_PET
            z = 0
            clear_others = False

        if not resource_id:
            return

        resolved_layer = normalize_layer(layer, Layer.MAIN_PET)
        request = DrawRequest(
            resource_id=resource_id,
            frame_index=frame_index,
            position=position,
            alpha=alpha,
            flipped=flipped,
            scale=scale,
            target_size=(
                Size(*ANIMATION['pet_size'])
                if resolved_layer == int(Layer.MAIN_PET)
                else None
            ),
            layer=resolved_layer,
            z=z,
        )

        self._draw_core.add_draw_request(request, clear_others=clear_others)

    def _handle_ui_create(self, event):
        """?? UI ??????"""
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')
        ui_id = event.data.get('ui_id')

        if window_id == 'pet_window':
            self._host_publish_anchor_response(
                window_id=window_id,
                anchor_id=anchor_id,
                ui_id=ui_id,
            )

        # ?? UI ??????? UI_CREATE ?????? PetWindow ??

    def _handle_clickthrough_toggle(self, event):
        """处理鼠标穿透模式切换事件"""
        enabled = event.data.get('enabled', False)
        self._clickthrough = enabled
        set_clickthrough_enabled(enabled)

        self._host_set_clickthrough(enabled)

    def _change_state(self, new_state: str):
        """切换状态"""
        self._state = new_state

        # 如果不是 moving 状态，重置翻转状态
        if new_state != 'moving':
            self._movement.flipped = False

        # 重置 DrawCore 的帧索引
        self._draw_core.reset_frame(new_state)

        # 发布绘制请求事件: [资源id, 资源帧[如有], 绘制位置]
        # clear_others=True 确保清除之前的绘制请求，避免重叠
        draw_event = Event(EventType.DRAW_REQUEST, {
            'resource_id': new_state,
            'frame_index': -1,  # 使用当前帧
            'position': None,   # 使用默认位置
            'alpha': _get_main_pet_opacity(),
            'flipped': self._movement.flipped,
            'scale': 1.0,
            'clear_others': True  # 清除其他所有绘制请求
        })
        self._event_center.publish(draw_event)

    # ==================================================================
    # BaseEntity 接口实现
    # ==================================================================

    def change_state(self, state: str):
        """切换到指定状态"""
        self._change_state(state)

    def get_current_state(self) -> str:
        """获取当前状态"""
        return self._state

    def start_move(self, target: Point):
        """开始移动到目标位置"""
        self._movement.start_move(target)

    def update_move_target(self, target: Point) -> None:
        """
        动态更新移动目标点（仅在移动中生效，不触发状态机切换）。

        供状态机在追踪动态目标（如跳跃中的雪豹）时每 TICK 调用，
        通过持续刷新 _target 实现平滑跟随，无需重新发起移动流程。
        """
        self._movement.update_move_target(target)

    def stop_move(self):
        """停止移动"""
        self._movement.stop_move()

    def play_animation(self, state: str, duration: int = 0):
        """播放指定动画,可选持续时间"""
        # 发布状态切换请求
        event = Event(EventType.STATE_CHANGE_REQUEST, {
            'new_state': state,
            'by_event': False
        })
        self._event_center.publish(event)
        if duration > 0:
            self.schedule_task(lambda: self._publish_state_change_request('idle', by_event=False), duration, repeat=False)

    def _publish_state_change_request(self, new_state: str, by_event: bool = True):
        """发布状态切换请求"""
        event = Event(EventType.STATE_CHANGE_REQUEST, {
            'new_state': new_state,
            'by_event': by_event
        })
        self._event_center.publish(event)

    def spawn_particles(self, x: int, y: int, particle_id: str = 'scatter_fall', area_type: str = 'point', area_data=None):
        """
        在指定位置生成粒子效果（通过事件中心发布申请）

        Args:
            x: X 坐标
            y: Y 坐标
            particle_id: 粒子ID（如 'scatter_fall', 'heart'）
            area_type: 区域类型（'point', 'rect', 'circle'）
            area_data: 区域数据
                - 如果是 'rect': (x1, y1, x2, y2) 矩形范围
                - 如果是 'circle': (x, y, radius) 圆形范围
                - 如果是 'point' 或 None: 使用 (x, y) 作为单点
        """
        # 构建区域数据
        if area_type == 'point' or area_data is None:
            area_data = (x, y)
        elif area_type == 'rect' and area_data:
            # 确保area_data是全局坐标
            pass
        elif area_type == 'circle' and area_data:
            # 确保area_data是全局坐标
            pass

        # 发布粒子申请事件
        event = Event(EventType.PARTICLE_REQUEST, {
            'particle_id': particle_id,
            'area_type': area_type,
            'area_data': area_data
        })
        self._event_center.publish(event)

    def toggle_command_dialog(self):
        """切换命令对话框显示状态"""
        self._host_toggle_command_dialog()

    def schedule_task(self, callback, delay_ms: int, repeat: bool = False):
        """
        调度任务

        Args:
            callback: 回调函数
            delay_ms: 延迟时间(毫秒)
            repeat: 是否重复

        Returns:
            任务ID
        """
        task_id = self._timing_manager.add_task(delay_ms, repeat=repeat)
        self._task_callbacks[task_id] = callback
        return task_id

    def cancel_task(self, task_id: str):
        """取消任务"""
        self._timing_manager.remove_task(task_id)
        self._task_callbacks.pop(task_id, None)

    def is_moving(self) -> bool:
        """返回当前是否处于移动中。"""
        return self._movement.is_moving

    def is_user_dragging(self) -> bool:
        """返回主宠当前是否正被鼠标左键拖拽。"""
        return self._movement.is_user_dragging

    def begin_user_drag(self) -> None:
        """进入用户拖拽状态，并立即清空当前移动队列。"""
        current_pos = self._movement.begin_user_drag()
        if current_pos is None:
            return
        self._move_particle_last_pos = current_pos
        self._move_particle_distance_accum = 0.0

    def update_user_drag_position(self, new_pos: Point) -> None:
        """由鼠标拖拽直接接管主宠位置。"""
        point = self._movement.update_user_drag_position(new_pos)
        if point is None:
            return

    def end_user_drag(self) -> None:
        """结束用户拖拽状态。"""
        current_pos = self._movement.end_user_drag()
        if current_pos is None:
            return
        self._move_particle_last_pos = current_pos
        self._move_particle_distance_accum = 0.0

    def set_direction(self, flipped: bool):
        """设置当前朝向（是否翻转）。"""
        self._movement.flipped = flipped

    def get_direction(self) -> bool:
        """返回当前朝向（是否翻转）。"""
        return self._movement.flipped

    def prepare_render(self):
        """更新绘制状态并将 DrawCore 交给窗口后端。"""
        self._draw_core.set_request_alpha(self._state, _get_main_pet_opacity())
        return self._draw_core

    def handle_pointer_enter(self) -> None:
        self._click_handler.handle_enter()

    def handle_pointer_leave(self) -> None:
        self._click_handler.handle_leave()

    def handle_pointer_press(self, event: MouseInput) -> None:
        self._click_handler.handle_press(event)

    def handle_pointer_move(self, event: MouseInput) -> None:
        self._click_handler.handle_move(event)

    def handle_pointer_release(self, button: MouseButton) -> None:
        self._mouse_event_handler.handle_release(button)

    def handle_window_moved(self, position: Point) -> None:
        self._track_move_particles(position)

    def handle_key_press(self, event: KeyboardInput) -> None:
        self._key_handler.handle_key_press(event)

    def handle_key_release(self, event: KeyboardInput) -> None:
        self._key_handler.handle_key_release(event)

    # ==================================================================
    # 移动系统 - 宿主副作用回调
    # ==================================================================

    def _on_movement_position_update(self, new_pos: Point):
        """移动控制器位置更新回调"""
        self._host_move(new_pos)
        # 发布锚点更新事件，通知 UI 组件更新位置
        anchor_update_event = Event(EventType.UI_ANCHOR_RESPONSE, {
            'window_id': 'pet_window',
            'anchor_id': 'all',
            'anchor_point': new_pos,
            'ui_id': 'all'
        })
        self._event_center.publish(anchor_update_event)

    def _on_direction_change(self, flipped: bool):
        """方向改变回调"""
        # 同步更新 DrawCore 的翻转状态
        self._draw_core.set_request_flipped(self._state, flipped)

    def _track_move_particles(self, new_pos: Point) -> None:
        """累计主宠物移动距离，每 30px 触发一次 flicker_data 粒子。"""
        point = coerce_point(new_pos) or Point()
        last_pos = coerce_point(self._move_particle_last_pos)
        if last_pos is None:
            self._move_particle_last_pos = point
            return

        dx = float(point.x - last_pos.x)
        dy = float(point.y - last_pos.y)
        step = math.hypot(dx, dy)
        self._move_particle_last_pos = point

        if step <= 0.0:
            return

        if not self._move_particle_enabled:
            self._move_particle_distance_accum = 0.0
            return

        self._move_particle_distance_accum += step
        while self._move_particle_distance_accum >= self._move_particle_step_px:
            self._move_particle_distance_accum -= self._move_particle_step_px
            geometry = self.get_core_geometry()
            cx = int(point.x + geometry.width / 2)
            cy = int(point.y + geometry.height / 2)
            self.spawn_particles(cx, cy, particle_id='flicker_data', area_type='point')

    def _spawn_teleport_burst_particles(self, origin_pos: Point) -> None:
        """在瞬移原地半径 30xp 内生成 5~8 个爆发线条粒子。"""
        radius_px = max(1, int(scale_px(30, min_abs=1)))
        burst_count = random.randint(5, 8)
        geometry = self.get_core_geometry()
        cx = int(origin_pos.x + geometry.width / 2)
        cy = int(origin_pos.y + geometry.height / 2)

        for _ in range(burst_count):
            angle = random.uniform(0.0, 2.0 * math.pi)
            dist = radius_px * math.sqrt(random.random())
            px = int(round(cx + math.cos(angle) * dist))
            py = int(round(cy + math.sin(angle) * dist))
            self.spawn_particles(px, py, particle_id='burst_line', area_type='point')

    def _schedule_teleport_burst(self, origin_pos: Point) -> None:
        """按 1~5 tick 延迟触发瞬移爆发线条粒子。"""
        delay_ticks = random.randint(1, 5)
        delay_ms = delay_ticks * TimingManager.TICK_INTERVAL_MS
        origin_copy = coerce_point(origin_pos) or Point()
        self.schedule_task(
            callback=lambda pos=origin_copy: self._spawn_teleport_burst_particles(pos),
            delay_ms=delay_ms,
            repeat=False,
        )

    # ==================================================================
    # 气泡
    # ==================================================================

    def _handle_frame_event(self, event):
        """处理帧事件 - 用于窗口位置更新"""
        alpha = float((event.data or {}).get('tick_alpha', 1.0) or 0.0)
        self._movement.update_frame(alpha)
        # 仅在窗口注册、注销或改层级后，于下一帧提交一次层级变化。
        get_layer_manager().enforce_on_frame()

    def _handle_tick_event(self, event):
        """处理 TICK 事件 - 用于速度计算。"""
        self._movement.update_tick()

    def _handle_entity_position_request(self, event):
        """
        处理实体位置请求事件 - 支持管理器解耦通信
        
        响应其他模块（如雪豹管理器）对主宠物位置/尺寸的查询。
        """
        entity_id = event.data.get('entity_id')
        request_id = event.data.get('request_id')

        if entity_id != 'pet_window':
            return

        pos = self.get_core_position()
        geom = self.get_core_geometry()

        self._event_center.publish(Event(EventType.ENTITY_POSITION_RESPONSE, {
            'entity_id': 'pet_window',
            'request_id': request_id,
            'position': pos,
            'size': (geom.width, geom.height),
        }))

    def _handle_entity_state_query(self, event):
        """
        处理实体状态查询事件 - 支持管理器解耦通信
        
        响应其他模块对主宠物状态的查询，如是否在移动、当前状态等。
        """
        entity_id = event.data.get('entity_id')
        request_id = event.data.get('request_id')

        if entity_id != 'pet_window':
            return

        query_type = event.data.get('query_type')

        if query_type == 'movement':
            self._event_center.publish(Event(EventType.ENTITY_STATE_RESPONSE, {
                'entity_id': 'pet_window',
                'request_id': request_id,
                'query_type': query_type,
                'is_moving': self._movement.is_moving,
                'target': self._movement.target if self._movement.is_moving else None,
            }))
        elif query_type == 'state':
            self._event_center.publish(Event(EventType.ENTITY_STATE_RESPONSE, {
                'entity_id': 'pet_window',
                'request_id': request_id,
                'query_type': query_type,
                'current_state': self._state,
                'flipped': self._movement.flipped,
            }))
        elif query_type == 'all':
            self._event_center.publish(Event(EventType.ENTITY_STATE_RESPONSE, {
                'entity_id': 'pet_window',
                'request_id': request_id,
                'query_type': query_type,
                'is_moving': self._movement.is_moving,
                'current_state': self._state,
                'flipped': self._movement.flipped,
                'position': self.get_core_position(),
            }))

    def _handle_pet_teleport(self, event: Event):
        """
        处理主宠物瞬移事件：立即移动到指定坐标。

        支持数据格式：
        - {'x': int/float, 'y': int/float}
        - {'position': Point 或其它 point-like 值}
        - {'position': (x, y)}
        - 可选 {'entity_id': 'pet_window'}（其它 entity_id 将忽略）
        """
        entity_id = event.data.get('entity_id')
        if entity_id and entity_id != 'pet_window':
            return

        target = event.data.get('position')
        tx = ty = None

        point = coerce_point(target)
        if point is not None:
            tx, ty = point.x, point.y
        else:
            tx = event.data.get('x')
            ty = event.data.get('y')

        try:
            x = int(round(float(tx)))
            y = int(round(float(ty)))
        except (TypeError, ValueError):
            _logger.warning("收到无效瞬移坐标: %r", event.data)
            return

        old_pos = self.get_core_position()
        self._schedule_teleport_burst(old_pos)

        new_pos = self._movement.teleport(Point(x, y))
        if new_pos is None:
            return
        self._move_particle_distance_accum = 0.0
        self._move_particle_last_pos = new_pos

        event.mark_handled()

    def _handle_gif_frame_event(self, event):
        """处理GIF帧事件 - 用于动画播放"""
        self._draw_core.set_request_alpha(self._state, _get_main_pet_opacity())
        # 更新 DrawCore 的帧
        result = self._draw_core.next_frame(self._state)

        if result:
            frame, loop_completed = result
            if loop_completed:
                # 发布GIF循环完成事件
                loop_event = Event(EventType.GIF_LOOP_COMPLETED, {
                    'state': self._state
                })
                self._event_center.publish(loop_event)

        # 触发重绘
        self._host_request_repaint()

    def _handle_timer_event(self, event):
        """处理定时器事件"""
        task_id = event.data.get('task_id')
        if task_id in self._task_callbacks:
            callback = self._task_callbacks[task_id]
            try:
                callback()
            except Exception as e:
                _logger.error("Task %s error: %s", task_id, e)

            # 如果任务不重复，清理回调映射
            if not event.data.get('repeat', True):
                self._task_callbacks.pop(task_id, None)

    def _request_app_quit(self):
        self.cleanup_core_state()
        self._event_center.publish(Event(EventType.APP_QUIT, {
            'entity': self,
            'exit_code': 0,
        }))

    def cleanup_core_state(self) -> None:
        """Stop backend-neutral pet state before the native host is destroyed."""
        if self._core_cleanup_done:
            return
        self._core_cleanup_done = True

        timing_manager = getattr(self, '_timing_manager', None)
        if timing_manager is not None:
            try:
                timing_manager.cleanup()
            except Exception:
                pass

        self._movement.cleanup()
        for event_type, callback in (
            (EventType.FRAME, self._handle_frame_event),
            (EventType.TICK, self._handle_tick_event),
            (EventType.GIF_FRAME, self._handle_gif_frame_event),
            (EventType.TIMER, self._handle_timer_event),
            (EventType.DRAW_REQUEST, self._handle_draw_request),
            (EventType.UI_CREATE, self._handle_ui_create),
            (EventType.UI_CLICKTHROUGH_TOGGLE, self._handle_clickthrough_toggle),
            (EventType.ENTITY_POSITION_REQUEST, self._handle_entity_position_request),
            (EventType.ENTITY_STATE_QUERY, self._handle_entity_state_query),
            (EventType.PET_TELEPORT, self._handle_pet_teleport),
        ):
            self._event_center.unsubscribe(event_type, callback)
        self._host_shutdown_ui()

    def handle_host_close(self) -> None:
        self.cleanup_core_state()


# ======================================================================
# 小狗窗口
# ======================================================================
