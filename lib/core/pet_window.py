"""宠物窗口模块"""
import math
import random
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore    import Qt, QTimer, QPoint
from PyQt5.QtGui     import QPainter

from config.config import COLORS, WINDOW, ANIMATION, GIF_FILES, BEHAVIOR, UI, PARTICLES
from lib.core.topmost_manager import get_topmost_manager
from lib.core.qt_gif_loader import scale_frame, flip_frame
from lib.core.qt_particle_system import ParticleOverlay
from lib.core.input.click import ClickHandler
from lib.core.input.key import KeyHandler
from lib.core.timing.manager import TimingManager
from lib.core.movement_controller import MovementController
from lib.core.event.mouse_handler import MouseEventHandler
from lib.core.logger import get_logger

_logger = get_logger(__name__)
from lib.core.voice.ams_startup import AmsStartupSound
from lib.core.event.key_handler import KeyEventHandler
from lib.core.event.app_handler import AppEventHandler
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.entity.base import BaseEntity
from lib.script.mainpet.state import StateMachine
from config.user_scale_config import get_user_scale_config
from lib.core.draw_core import DrawRequest, get_draw_core
from lib.core.action import Actions
from lib.core.pet_window_ui_factory import attach_pet_window_ui, iter_pet_window_ui
from lib.core.pet_window_setup import setup_pet_window, finalize_pet_window_startup
from lib.core.timing import register_timing_manager
from lib.core.anchor_utils import (
    get_anchor_point as resolve_anchor_point,
    publish_widget_anchor_response,
)
from config.scale import scale_px
from lib.script.ui.shutdown import hide_all_runtime_ui


def _get_main_pet_opacity() -> float:
    raw_value = UI.get('pet_opacity', 1.0)
    try:
        opacity = float(raw_value)
    except (TypeError, ValueError):
        opacity = 1.0
    return max(0.0, min(1.0, opacity))


class PetWindow(BaseEntity):
    """
    主宠物窗口：无边框、置顶、透明背景。
    所有渲染通过 paintEvent 完成，不依赖 transparentcolor hack。
    """

    def __init__(self, gifs: dict, particle_overlay: ParticleOverlay):
        super().__init__()

        self._gifs    = gifs
        self._particles = particle_overlay

        # ── 绘制核心 ───────────────────────────────────────────────────
        self._draw_core = get_draw_core()

        # ── 移动控制器 ──────────────────────────────────────────────────
        self._movement = MovementController(
            on_position_update=self._on_movement_position_update,
            on_move_complete=self._on_movement_complete,
            on_direction_change=self._on_direction_change
        )

        # 鼠标穿透状态
        self._clickthrough = False

        # ── 输入处理器 ────────────────────────────────────────────────
        self._click_handler = ClickHandler(self)
        self._key_handler = KeyHandler(self)

        # ── 事件处理器 ───────────────────────────────────────────────
        self._mouse_event_handler = MouseEventHandler(self)
        self._key_event_handler = KeyEventHandler(self)
        self._app_event_handler = AppEventHandler(self)
        self._startup_voice_sound = AmsStartupSound(interruptible=False)

        # ── 计时器管理器 ──────────────────────────────────────────────
        self._timing_manager = TimingManager(
            frame_fps=ANIMATION['frame_fps'],
            gif_fps=ANIMATION['gif_fps']
        )
        self._timing_manager.start()

        # 注册全局访问器，供子对象（雪豹、雪堆等）使用 add_task
        register_timing_manager(self._timing_manager)

        # ── 事件中心 ─────────────────────────────────────────────────
        self._event_center = get_event_center()

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
        self._event_center.subscribe(EventType.DRAW_RENDER, self._handle_draw_render)

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

        # ── 状态 ──────────────────────────────────────────────────────
        self._state = 'idle'

        # ── 移动任务ID ──────────────────────────────────────────────
        self._move_task_id = None

        # ── 移动粒子：累计位移每 30px 触发一次 flicker_data ─────────────
        self._move_particle_step_px = 30.0
        self._move_particle_distance_accum = 0.0
        self._move_particle_last_pos = None
        self._move_particle_enabled = False

        # 鈹€鈹€ 鍒濆浣嶇疆 / UI / 绐楀彛灞炴€?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        setup_pet_window(self)
        attach_pet_window_ui(self, on_close=self._request_app_quit)


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
        finalize_pet_window_startup(self)

    def _register_all_resources(self):
        """将所有 GIF 资源注册到 DrawCore"""
        for resource_id, frames in self._gifs.items():
            self._draw_core.register_resource(resource_id, frames)

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
            clear_others = data.get('clear_others', False)
        # 格式2: [资源id, 资源帧[如有], 绘制位置]
        else:
            resource_id = data[0] if len(data) > 0 else None
            frame_index = data[1] if len(data) > 1 else -1
            position = data[2] if len(data) > 2 else None
            alpha = 1.0
            flipped = self._movement.flipped
            scale = 1.0
            clear_others = False

        if not resource_id:
            return

        request = DrawRequest(
            resource_id=resource_id,
            frame_index=frame_index,
            position=position,
            alpha=alpha,
            flipped=flipped,
            scale=scale
        )

        self._draw_core.add_draw_request(request, clear_others=clear_others)

    def _handle_draw_render(self, event):
        """处理绘制渲染事件"""
        painter = event.data.get('painter')
        target_rect = event.data.get('target_rect')
        if painter:
            self._draw_core.render(painter, target_rect)

    def _handle_ui_create(self, event):
        """?? UI ??????"""
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')
        ui_id = event.data.get('ui_id')

        if window_id == 'pet_window':
            publish_widget_anchor_response(
                self._event_center,
                self,
                window_id=window_id,
                anchor_id=anchor_id,
                ui_id=ui_id,
            )

        # ?? UI ??????? UI_CREATE ?????? PetWindow ??

    def _handle_clickthrough_toggle(self, event):
        """处理鼠标穿透模式切换事件"""
        enabled = event.data.get('enabled', False)
        self._clickthrough = enabled

        # 保存基础窗口标志
        base_flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool

        if enabled:
            # 启用鼠标穿透
            # 先隐藏窗口
            self.hide()
            # 设置穿透属性
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            # 移除 WindowSystemMenuHint 标志，因为它可能与穿透属性冲突
            self.setWindowFlags(base_flags)
            # 显示窗口
            self.show()
        else:
            # 禁用鼠标穿透
            # 先隐藏窗口
            self.hide()
            # 设置非穿透属性
            self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            # 恢复 WindowSystemMenuHint 标志（确保 mouseMoveEvent 能够正确触发）
            self.setWindowFlags(base_flags | Qt.WindowSystemMenuHint)
            # 显示窗口
            self.show()

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

    def start_move(self, target: QPoint):
        """开始移动到目标位置"""
        self._start_move(target)

    def update_move_target(self, target: QPoint) -> None:
        """
        动态更新移动目标点（仅在移动中生效，不触发状态机切换）。

        供状态机在追踪动态目标（如跳跃中的雪豹）时每 TICK 调用，
        通过持续刷新 _target 实现平滑跟随，无需重新发起移动流程。
        """
        self._movement.update_target(target)

    def stop_move(self):
        """停止移动"""
        self._movement.stop_move()
        # 发布回到idle的请求
        event = Event(EventType.STATE_CHANGE_REQUEST, {
            'new_state': 'idle',
            'by_event': False
        })
        self._event_center.publish(event)

    def get_position(self) -> QPoint:
        """获取当前位置"""
        return self.frameGeometry().topLeft()

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
        self._cmd.toggle(self)

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

    def get_geometry(self):
        """获取窗口几何信息"""
        return self.frameGeometry()

    def is_moving(self) -> bool:
        """返回当前是否处于移动中。"""
        return self._movement.is_moving

    def set_direction(self, flipped: bool):
        """设置当前朝向（是否翻转）。"""
        self._movement.flipped = flipped

    def get_direction(self) -> bool:
        """返回当前朝向（是否翻转）。"""
        return self._movement.flipped

    def get_anchor_point(self, anchor_id: str) -> QPoint:
        """
        获取指定锚点的位置

        Args:
            anchor_id: 锚点 ID ('top', 'bottom', 'left', 'right', 
                        'top_left', 'top_right', 'bottom_left', 'bottom_right', 'center')

        Returns:
            锚点位置（相对于窗口的坐标）
        """
        return resolve_anchor_point(self, anchor_id)

    def paintEvent(self, event):
        """绘制事件 - 使用 DrawCore 统一绘制"""
        painter = QPainter(self)
        self._draw_core.set_request_alpha(self._state, _get_main_pet_opacity())
        self._draw_core.render(painter, self.rect())

    # ==================================================================
    # 鼠标事件
    # ==================================================================

    def enterEvent(self, event):
        self._click_handler.handle_enter(event)

    def leaveEvent(self, event):
        self._click_handler.handle_leave(event)

    def mousePressEvent(self, event):
        self._click_handler.handle_press(event)

    def mouseMoveEvent(self, event):
        self._click_handler.handle_move(event)

    def moveEvent(self, event):
        """窗口移动事件：累计位移并按阈值触发移动粒子。"""
        super().moveEvent(event)
        self._track_move_particles(QPoint(event.pos()))

    # ==================================================================
    # 键盘事件
    # ==================================================================

    def keyPressEvent(self, event):
        self._key_handler.handle_key_press(event)

    def keyReleaseEvent(self, event):
        self._key_handler.handle_key_release(event)

    # ==================================================================
    # 移动系统 - 委托给 MovementController
    # ==================================================================

    def _start_move(self, target: QPoint):
        """开始移动到目标位置"""
        self._movement.start_move(target)
        # 发布切换到moving的请求
        event = Event(EventType.STATE_CHANGE_REQUEST, {
            'new_state': 'moving',
            'by_event': False
        })
        self._event_center.publish(event)

    def _start_move_timer(self, target: QPoint):
        """供状态机调用的移动启动方法"""
        self._start_move(target)

    def _on_movement_position_update(self, new_pos: QPoint):
        """移动控制器位置更新回调"""
        self.move(new_pos)
        # 发布锚点更新事件，通知 UI 组件更新位置
        anchor_update_event = Event(EventType.UI_ANCHOR_RESPONSE, {
            'window_id': 'pet_window',
            'anchor_id': 'all',
            'anchor_point': new_pos,
            'ui_id': 'all'
        })
        self._event_center.publish(anchor_update_event)

    def _on_movement_complete(self):
        """移动完成回调"""
        self._event_center.publish(Event(EventType.STATE_CHANGE_REQUEST, {
            'new_state': 'idle',
            'by_event': True
        }))

    def _on_direction_change(self, flipped: bool):
        """方向改变回调"""
        # 同步更新 DrawCore 的翻转状态
        self._draw_core.set_request_flipped(self._state, flipped)

    def _track_move_particles(self, new_pos: QPoint) -> None:
        """累计主宠物移动距离，每 30px 触发一次 flicker_data 粒子。"""
        if self._move_particle_last_pos is None:
            self._move_particle_last_pos = QPoint(new_pos)
            return

        dx = float(new_pos.x() - self._move_particle_last_pos.x())
        dy = float(new_pos.y() - self._move_particle_last_pos.y())
        step = math.hypot(dx, dy)
        self._move_particle_last_pos = QPoint(new_pos)

        if step <= 0.0:
            return

        if not self._move_particle_enabled:
            self._move_particle_distance_accum = 0.0
            return

        self._move_particle_distance_accum += step
        while self._move_particle_distance_accum >= self._move_particle_step_px:
            self._move_particle_distance_accum -= self._move_particle_step_px
            cx = int(new_pos.x() + self.width() / 2)
            cy = int(new_pos.y() + self.height() / 2)
            self.spawn_particles(cx, cy, particle_id='flicker_data', area_type='point')

    def _spawn_teleport_burst_particles(self, origin_pos: QPoint) -> None:
        """在瞬移原地半径 30xp 内生成 5~8 个爆发线条粒子。"""
        radius_px = max(1, int(scale_px(30, min_abs=1)))
        burst_count = random.randint(5, 8)
        cx = int(origin_pos.x() + self.width() / 2)
        cy = int(origin_pos.y() + self.height() / 2)

        for _ in range(burst_count):
            angle = random.uniform(0.0, 2.0 * math.pi)
            dist = radius_px * math.sqrt(random.random())
            px = int(round(cx + math.cos(angle) * dist))
            py = int(round(cy + math.sin(angle) * dist))
            self.spawn_particles(px, py, particle_id='burst_line', area_type='point')

    def _schedule_teleport_burst(self, origin_pos: QPoint) -> None:
        """按 1~5 tick 延迟触发瞬移爆发线条粒子。"""
        delay_ticks = random.randint(1, 5)
        delay_ms = delay_ticks * TimingManager.TICK_INTERVAL_MS
        origin_copy = QPoint(origin_pos)
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
        if self._movement.is_moving:
            self._movement.update_frame(self.frameGeometry().topLeft())
        # 每帧即时置顶（Qt 层）+ 每 0.5s 对全部注册窗口重申 Win32 HWND_TOPMOST
        # 注意：穿透模式下仍需置顶以保证桌宠可见，只是鼠标事件穿透
        self.raise_()
        get_topmost_manager().enforce_on_frame()

    def _handle_tick_event(self, event):
        """处理 TICK 事件 - 用于速度计算。"""
        if self._movement.is_moving:
            self._movement.update_tick(self.frameGeometry().topLeft())

    def _handle_entity_position_request(self, event):
        """
        处理实体位置请求事件 - 支持管理器解耦通信
        
        响应其他模块（如雪豹管理器）对主宠物位置/尺寸的查询。
        """
        entity_id = event.data.get('entity_id')
        request_id = event.data.get('request_id')

        if entity_id != 'pet_window':
            return

        pos = self.get_position()
        geom = self.get_geometry()

        self._event_center.publish(Event(EventType.ENTITY_POSITION_RESPONSE, {
            'entity_id': 'pet_window',
            'request_id': request_id,
            'position': pos,
            'size': (geom.width(), geom.height()),
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
                'position': self.get_position(),
            }))

    def _handle_pet_teleport(self, event: Event):
        """
        处理主宠物瞬移事件：立即移动到指定坐标。

        支持数据格式：
        - {'x': int/float, 'y': int/float}
        - {'position': QPoint}
        - {'position': (x, y)}
        - 可选 {'entity_id': 'pet_window'}（其它 entity_id 将忽略）
        """
        entity_id = event.data.get('entity_id')
        if entity_id and entity_id != 'pet_window':
            return

        target = event.data.get('position')
        tx = ty = None

        if isinstance(target, QPoint):
            tx, ty = target.x(), target.y()
        elif isinstance(target, (list, tuple)) and len(target) >= 2:
            tx, ty = target[0], target[1]
        else:
            tx = event.data.get('x')
            ty = event.data.get('y')

        try:
            x = int(round(float(tx)))
            y = int(round(float(ty)))
        except (TypeError, ValueError):
            _logger.warning("收到无效瞬移坐标: %r", event.data)
            return

        old_pos = QPoint(self.frameGeometry().topLeft())
        self._schedule_teleport_burst(old_pos)

        if self._movement.is_moving:
            self._movement.stop_move()
            self._event_center.publish(Event(EventType.STATE_CHANGE_REQUEST, {
                'new_state': 'idle',
                'by_event': False
            }))

        self.move(x, y)
        self._move_particle_distance_accum = 0.0
        self._move_particle_last_pos = QPoint(self.frameGeometry().topLeft())

        # 立即同步锚点，避免附属 UI 等待下一次位置回调。
        self._event_center.publish(Event(EventType.UI_ANCHOR_RESPONSE, {
            'window_id': 'pet_window',
            'anchor_id': 'all',
            'anchor_point': QPoint(x, y),
            'ui_id': 'all'
        }))

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
        self.update()

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

    def _preload_ui(self):
        """预加载 UI 组件 - 触发一次 UI 显示和隐藏以初始化动画和资源"""
        # 延迟 500ms 后开始预加载，确保宠物窗口已经完全初始化
        preload_task_id = self.schedule_task(self._do_preload_ui, 500, repeat=False)

    def _do_preload_ui(self):
        """执行 UI 预加载 - 触发淡入动画后立即淡出，仅用于预热，不持续占据显示"""
        # 显示命令对话框（触发淡入动画）
        self._cmd.toggle(self)
        # 在淡入动画结束后立即隐藏（fade_duration + 50ms 缓冲），避免预加载窗口
        # 长期可见导致用户首次右键点击时反向关闭对话框
        hide_delay = UI['ui_fade_duration'] + 50
        self.schedule_task(self._do_preload_hide, hide_delay, repeat=False)

    def _do_preload_hide(self):
        """预加载完成后隐藏命令框"""
        if self._cmd._visible:
            self._cmd.toggle(None)

    def _shutdown_ui_widgets(self):
        for attr_name, widget in iter_pet_window_ui(self):
            if widget is None:
                continue
            try:
                widget.hide()
            except Exception:
                pass
            try:
                widget.close()
            except Exception:
                pass

        try:
            self.hide()
        except Exception:
            pass

    def _request_app_quit(self):
        timing_manager = getattr(self, '_timing_manager', None)
        if timing_manager is not None:
            try:
                timing_manager.stop()
            except Exception:
                pass

        self._shutdown_ui_widgets()
        hide_all_runtime_ui()
        self._event_center.publish(Event(EventType.APP_QUIT, {
            'entity': self,
            'exit_code': 0,
        }))

    def closeEvent(self, event):
        self._shutdown_ui_widgets()
        super().closeEvent(event)


# ======================================================================
# 小狗窗口
# ======================================================================
