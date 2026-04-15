"""单个摩托对象 - 可拖拽投掷、双击淡出、带物理弹跳的摩托小窗口"""
import random
import time
from collections import deque

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore    import Qt, QPoint
from PyQt5.QtGui     import QPainter, QPixmap

from config.config            import BEHAVIOR, PHYSICS, MORTOR
from lib.core.topmost_manager  import get_topmost_manager
from lib.core.event.center     import get_event_center, EventType, Event
from lib.core.clickthrough_state import is_clickthrough_enabled
from lib.core.physics          import get_physics_world, PhysicsBody
from lib.core.screen_utils     import get_screen_geometry_for_point
from lib.core.voice.chrack     import ChrackSound


# 从配置文件读取物理参数
_GROUND_Y_PCT: float = PHYSICS.get('ground_y_pct', 0.90)
_MAX_THROW_VX: float = PHYSICS.get('max_throw_vx', 25.0)
_MAX_THROW_VY: float = PHYSICS.get('max_throw_vy', 25.0)
_DRAG_THRESHOLD: int = PHYSICS.get('drag_threshold', 5)
_FADE_STEP: float = PHYSICS.get('fade_step', 0.05)
_FADE_INTERVAL_MS: int = PHYSICS.get('fade_interval_ms', 50)
_MAX_BOUNCES: int = PHYSICS.get('max_bounces', 5)
_DRAG_TRAIL_WINDOW_SEC: float = 0.10
_RELEASE_SAMPLE_MIN_DT_SEC: float = 1.0 / 60.0
_BASE_MOVE_SPEED: float = float(MORTOR.get('move_speed_px_per_frame', 2.0))
_ACCEL_PER_TICK: float = float(MORTOR.get('move_accel_per_tick', 1.0))
_DECEL_PER_TICK: float = float(MORTOR.get('move_decel_per_tick', 2.0))
_MAX_MOVE_SPEED: float = float(MORTOR.get('move_speed_max', 10.0))
_JUMP_VY: float = float(MORTOR.get('jump_vy', PHYSICS.get('snow_leopard_jump_vy', -13.0)))
_JUMP_COOLDOWN_SEC: float = float(MORTOR.get('jump_cooldown_sec', 2.0))
_JUMP_MAX_CHARGES: int = int(MORTOR.get('jump_max_charges', 2))
_GROUND_EPSILON: float = 1.0
_IDLE_JITTER_PX: int = 1
_MOVE_JITTER_PX: int = 3
_JITTER_HALF_SCALE: float = 0.5


class Mortor(QWidget):
    """
    单个摩托窗口。

    - 左键按住拖拽：移动摩托到任意位置，松开时继承拖拽速度（可"丢出"）
    - 左键双击：淡出消失
    - 方向键控制：左/右键切换朝向并驱动物理水平速度（按住加速，松开减速）
    - ↑ 键跳跃：2 秒冷却充能，最多 2 层，可空中跳跃
    - 渲染抖动：相对原先幅度减半（静止约 ±0.5px，移动约 ±1.5px）
    - 物理弹跳：地面为屏幕高度 90%，最多弹跳 5 次，会与左右屏幕边界碰撞
    - 重新拖拽：中断物理、重置弹跳计数
    """

    # 使用模块级配置变量（已从 PHYSICS 配置读取）

    def __init__(self,
                 pixmap: QPixmap,
                 flipped_pixmap: QPixmap,
                 position: QPoint,
                 size: tuple):
        """
        Args:
            pixmap:         正向 QPixmap（已缩放至目标尺寸）
            flipped_pixmap: 水平翻转 QPixmap
            position:       屏幕全局坐标（左上角）
            size:           窗口尺寸 (width, height)
        """
        super().__init__()

        self._pixmap         = pixmap
        self._flipped_pixmap = flipped_pixmap
        self._size           = size
        self._flipped        = False  # 默认朝右（右方向）
        self._move_dir       = 1      # 1=右，-1=左
        self._move_speed     = 0.0
        self._left_pressed   = False
        self._right_pressed  = False
        self._up_pressed     = False
        self._jump_charges   = max(1, _JUMP_MAX_CHARGES)
        self._next_jump_charge_time: float | None = None
        self._render_jitter  = QPoint(0, 0)
        self._alive          = True
        self._alpha          = 1.0
        self._fading         = False
        self._fade_tick_stride = max(1, int(round(_FADE_INTERVAL_MS / 50.0)))
        self._fade_tick_count = 0

        # 拖拽 / 点击判定状态
        self._press_pos: QPoint | None   = None  # 按下时的全局坐标（None = 未按下）
        self._drag_offset: QPoint | None = None  # 拖拽基准偏移（None = 尚未进入拖拽）
        # 速度轨迹队列：存储 (monotonic_time, QPoint) 对，仅保留最近 100ms 数据
        self._drag_trail: deque = deque()

        # ── 窗口属性 ──────────────────────────────────────────────
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, is_clickthrough_enabled())
        self.setFixedSize(*size)
        self.setCursor(Qt.OpenHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        # ── 物理体 ────────────────────────────────────────────────
        w, h     = size
        spawn_center = QPoint(position.x() + w // 2, position.y() + h // 2)
        screen_geom = get_screen_geometry_for_point(spawn_center)
        ground_y = screen_geom.y() + screen_geom.height() * _GROUND_Y_PCT - h  # 窗口左上角落地 Y

        self._physics_body = PhysicsBody(
            x           = float(position.x()),
            y           = float(position.y()),
            ground_y    = ground_y,
            width       = w,
            height      = h,
            max_bounces = _MAX_BOUNCES,
        )
        self._physics_body.on_position_change = self._on_physics_position_change
        self._physics_body.on_wall_hit        = self._on_physics_wall_hit
        self._physics_body.on_ground_bounce   = self._on_physics_ground_bounce

        self._physics_cleaned = False
        get_physics_world().add_body(self._physics_body)

        # ── 双击判定（与 ClickHandler 相同的 TICK 计数机制）────────
        # 待确认的单击状态（True = 有一次左键按下待确认）
        self._pending_click       = False
        # 自首次按下已累积的 tick 数
        self._pending_click_ticks = 0
        # 双击判定间隔（tick 数），读取全局配置，与 ClickHandler 保持一致
        self._double_click_ticks  = BEHAVIOR.get('double_click_ticks', 3)

        self.move(position)
        self.show()
        get_topmost_manager().register(self)
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)

        # 事件中心
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.TICK,                   self._on_tick_click)
        self._event_center.subscribe(EventType.TICK,                   self._on_tick_motion)
        self._event_center.subscribe(EventType.TICK,                   self._on_tick_render_jitter)
        self._event_center.subscribe(EventType.KEY_PRESS,              self._on_key_press)
        self._event_center.subscribe(EventType.KEY_RELEASE,            self._on_key_release)
        self._event_center.subscribe(EventType.FRAME,                  self._on_frame_move)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        # 弹跳音效
        self._chrack_sound = ChrackSound()

        # 召唤时立即激活物理体，使其下落
        self._physics_body.active = True

    # ==================================================================
    # 公开接口
    # ==================================================================

    def get_center(self) -> QPoint:
        """返回摩托锚点的全局屏幕坐标（几何中心向上偏移 30px）。"""
        return QPoint(
            self.x() + self._size[0] // 2,
            self.y() + self._size[1] // 2 - 30,
        )

    def is_alive(self) -> bool:
        """是否仍然存活（未关闭）。"""
        return self._alive

    def set_gravity_enabled(self, enabled: bool):
        """
        设置重力开关状态。

        关闭重力时，物体不再受重力影响（不会下落），
        但物理系统其他功能正常（投掷、边界碰撞等）。

        Args:
            enabled: True 开启重力，False 关闭重力
        """
        if self._fading:
            return
        self._physics_body.gravity_enabled = enabled
        # 关闭重力时重置垂直速度，防止继续下落
        if not enabled:
            self._physics_body.vy = 0.0

    # ==================================================================
    # 淡出（使用项目 TICK 事件驱动，与雪豹/雪堆保持一致）
    # ==================================================================

    def start_fadeout(self):
        """触发淡出消失（幂等，重复调用安全）。"""
        if self._fading:
            return
        self._fading              = True
        self._press_pos           = None
        self._drag_offset         = None
        self._pending_click       = False
        self._pending_click_ticks = 0
        self._fade_tick_count = 0
        self._event_center.unsubscribe(EventType.TICK, self._on_tick_click)
        self._event_center.unsubscribe(EventType.TICK, self._on_tick_motion)
        self._event_center.unsubscribe(EventType.TICK, self._on_tick_render_jitter)
        self._cleanup_physics()
        self._event_center.subscribe(EventType.TICK, self._tick_fade)

    def _tick_fade(self, event: Event):
        """TICK 事件回调（淡出阶段）：逐步降低透明度直至关闭。"""
        self._fade_tick_count += 1
        if self._fade_tick_count < self._fade_tick_stride:
            return
        self._fade_tick_count = 0
        self._alpha -= _FADE_STEP
        if self._alpha <= 0.0:
            self._alpha = 0.0
            self._event_center.unsubscribe(EventType.TICK, self._tick_fade)
            self._alive = False
            self.close()
        else:
            self.update()

    # ==================================================================
    # 物理资源
    # ==================================================================

    def _cleanup_physics(self) -> None:
        """停止物理模拟并从物理世界注销（幂等）。"""
        if self._physics_cleaned:
            return
        self._physics_cleaned     = True
        self._physics_body.active = False
        get_physics_world().remove_body(self._physics_body)

    # ==================================================================
    # 物理回调
    # ==================================================================

    def _on_physics_position_change(self, body: PhysicsBody) -> None:
        """物理步进后同步窗口位置到新坐标。"""
        if not self._fading:
            self.move(QPoint(int(body.x), int(body.y)))

    def _on_physics_wall_hit(self, body: PhysicsBody, side: str) -> None:
        """碰墙时仅播放弹跳音效（不触发翻转和粒子）。"""
        self._chrack_sound.play()

    def _on_physics_ground_bounce(self, body: PhysicsBody, stopped: bool) -> None:
        """触地时仅播放弹跳音效（不触发粒子）。"""
        self._chrack_sound.play()

    # ==================================================================
    # 双击判定（与 ClickHandler 相同的 TICK 计数机制）
    # ==================================================================

    def _on_tick_click(self, event: Event):
        """
        TICK 事件回调：双击超时判定。

        与 ClickHandler._on_tick 逻辑完全一致：
        每 TICK 计数加一，达到 double_click_ticks 则超时，确认为单击（无额外行为）。
        """
        if not self._pending_click:
            return
        self._pending_click_ticks += 1
        if self._pending_click_ticks >= self._double_click_ticks:
            self._pending_click       = False
            self._pending_click_ticks = 0

    # ==================================================================
    # 事件响应
    # ==================================================================

    def _on_key_press(self, event: Event) -> None:
        """响应方向键：切换朝向与移动方向。"""
        if event.handled or self._fading:
            return
        if event.data.get('is_auto_repeat', False):
            return
        key = event.data.get('key')
        if key == Qt.Key_Left:
            self._left_pressed = True
            self._move_dir = -1
            self._flipped = True
            self.update()
        elif key == Qt.Key_Right:
            self._right_pressed = True
            self._move_dir = 1
            self._flipped = False
            self.update()
        elif key == Qt.Key_Up:
            if not self._up_pressed:
                self._up_pressed = True
                self._request_jump()

    def _on_key_release(self, event: Event) -> None:
        """响应方向键释放：停止对应方向的持续移动。"""
        if event.data.get('is_auto_repeat', False):
            return
        key = event.data.get('key')
        if key == Qt.Key_Left:
            self._left_pressed = False
        elif key == Qt.Key_Right:
            self._right_pressed = False
        elif key == Qt.Key_Up:
            self._up_pressed = False

    def _on_tick_motion(self, event: Event) -> None:
        """按 tick 更新方向键目标速度（按住加速，松开减速）。"""
        if self._fading or not self._alive:
            return
        self._recharge_jump_charges()
        if self._drag_offset is not None:
            return

        input_dir = 0
        if self._left_pressed and not self._right_pressed:
            input_dir = -1
        elif self._right_pressed and not self._left_pressed:
            input_dir = 1

        if input_dir != 0:
            self._move_dir = input_dir
            self._flipped = (input_dir < 0)
            if self._move_speed <= 0.0:
                self._move_speed = _BASE_MOVE_SPEED
            else:
                self._move_speed = min(_MAX_MOVE_SPEED, self._move_speed + _ACCEL_PER_TICK)
            return

        self._move_speed = max(0.0, self._move_speed - _DECEL_PER_TICK)

    def _on_tick_render_jitter(self, event: Event) -> None:
        """按 tick 更新渲染抖动偏移（相对原先幅度减半）。"""
        if self._fading or not self._alive:
            return

        is_moving = (
            self._move_speed > 0.0
            or self._drag_offset is not None
            or (self._physics_body.active and (
                abs(self._physics_body.vx) > 0.01
                or abs(self._physics_body.vy) > 0.01
            ))
        )
        base_amp = _MOVE_JITTER_PX if is_moving else _IDLE_JITTER_PX
        amp = self._scaled_jitter_amp(base_amp)
        self._render_jitter = QPoint(
            random.randint(-amp, amp),
            random.randint(-amp, amp),
        )
        self.update()

    @staticmethod
    def _scaled_jitter_amp(base_amp: int) -> int:
        """
        将整数像素抖动幅度按比例缩小。

        在像素坐标下无法直接使用 0.5/1.5 这样的半像素，
        因此使用上下取整的随机混合，使期望值接近 base_amp * 0.5。
        """
        if base_amp <= 0:
            return 0
        scaled = float(base_amp) * _JITTER_HALF_SCALE
        low = int(scaled)
        high = low if scaled == low else low + 1
        if high == low:
            return low
        p_high = scaled - low
        return high if random.random() < p_high else low

    def _on_frame_move(self, event: Event) -> None:
        """每帧将当前方向键速度应用到物理体（物理驱动）。"""
        if self._fading or not self._alive:
            return

        # 拖拽中不执行键控物理
        if self._drag_offset is not None:
            return

        self._apply_keyboard_physics()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    # ==================================================================
    # 内部辅助
    # ==================================================================

    def _get_current_pixmap(self) -> QPixmap:
        """根据翻转状态返回当前 QPixmap。"""
        return self._flipped_pixmap if self._flipped else self._pixmap

    def _is_airborne(self) -> bool:
        """是否离地（含上升/下降过程）。"""
        body = self._physics_body
        return (body.y < body.ground_y - _GROUND_EPSILON) or (abs(body.vy) > 1.0)

    def _request_jump(self) -> None:
        """消耗一层跳跃充能并触发跳跃（允许空中跳）。"""
        if self._drag_offset is not None:
            return
        if self._jump_charges <= 0:
            return

        self._jump_charges -= 1
        if self._jump_charges < _JUMP_MAX_CHARGES and self._next_jump_charge_time is None:
            self._next_jump_charge_time = time.monotonic() + _JUMP_COOLDOWN_SEC

        body = self._physics_body
        body.x = float(self.x())
        body.y = float(self.y())
        body.bounce_count = 0
        body.gravity_enabled = True
        body.active = True
        body.vy = _JUMP_VY
        if self._move_speed > 0.0:
            body.vx = self._move_dir * self._move_speed
    
    def _recharge_jump_charges(self) -> None:
        """按冷却时间恢复跳跃充能。"""
        if self._jump_charges >= _JUMP_MAX_CHARGES:
            self._next_jump_charge_time = None
            return

        now = time.monotonic()
        if self._next_jump_charge_time is None:
            self._next_jump_charge_time = now + _JUMP_COOLDOWN_SEC
            return

        while self._jump_charges < _JUMP_MAX_CHARGES and now >= self._next_jump_charge_time:
            self._jump_charges += 1
            if self._jump_charges < _JUMP_MAX_CHARGES:
                self._next_jump_charge_time += _JUMP_COOLDOWN_SEC
            else:
                self._next_jump_charge_time = None

    def _apply_keyboard_physics(self) -> None:
        """将方向键速度写入物理体，由 PhysicsWorld 统一推进位置。"""
        body = self._physics_body
        # 同步当前位置，避免长时间 idle 后首次加速出现位移跳变
        body.x = float(self.x())
        body.y = float(self.y())

        if self._is_airborne():
            # 空中阶段始终受重力；可保留水平控制
            body.gravity_enabled = True
            if self._move_speed > 0.0:
                body.vx = self._move_dir * self._move_speed
            body.active = True
            return

        if self._move_speed > 0.0:
            # 地面行驶：关闭重力，仅用 vx 驱动，避免地面微弹跳
            body.gravity_enabled = False
            body.vy = 0.0
            body.vx = self._move_dir * self._move_speed
            body.active = True
            return

        # 无输入且已接地：停稳
        body.gravity_enabled = True
        body.vx = 0.0
        body.vy = 0.0
        body.active = False

    def _compute_release_velocity(self, release_pos: QPoint) -> tuple[float, float]:
        """按松手瞬时采样计算投掷速度。"""
        now = time.monotonic()
        self._drag_trail.append((now, release_pos))

        cutoff = now - _DRAG_TRAIL_WINDOW_SEC
        while self._drag_trail and self._drag_trail[0][0] < cutoff:
            self._drag_trail.popleft()

        if len(self._drag_trail) < 2:
            return 0.0, 0.0

        t1, p1 = self._drag_trail[-1]
        idx = len(self._drag_trail) - 2
        t0, p0 = self._drag_trail[idx]
        while idx > 0 and (t1 - t0) < _RELEASE_SAMPLE_MIN_DT_SEC:
            idx -= 1
            t0, p0 = self._drag_trail[idx]

        dt_ms = (t1 - t0) * 1000.0
        if dt_ms <= 0:
            return 0.0, 0.0

        dp = p1 - p0
        vx = dp.x() / dt_ms * (1000.0 / 60.0)
        vy = dp.y() / dt_ms * (1000.0 / 60.0)
        return vx, vy

    # ==================================================================
    # Qt 事件
    # ==================================================================

    def mousePressEvent(self, event):
        """
        左键按下：
          · 双击判定间隔内（_pending_click=True）再次按下 → 淡出消失
          · 首次按下（_pending_click=False）→ 进入待确认状态、中断物理、记录按下位置

        拖拽提交延迟到 mouseMoveEvent：移动 ≥ _DRAG_THRESHOLD 像素才正式进入拖拽，
        避免点击/双击被拖拽逻辑拦截。
        """
        if not self._fading:
            self.activateWindow()
            self.setFocus(Qt.MouseFocusReason)

        if event.button() == Qt.LeftButton and not self._fading:
            if self._pending_click:
                # 双击间隔内二次按下 → 双击确认 → 淡出
                self._pending_click       = False
                self._pending_click_ticks = 0
                self._press_pos           = None
                self._drag_offset         = None
                self.start_fadeout()
                return
            # 首次按下：进入待确认状态（与 ClickHandler.handle_press 逻辑一致）
            self._pending_click       = True
            self._pending_click_ticks = 0
            # 中断当前物理，重置弹跳次数（重新拖拽给满额 5 次机会）
            self._physics_body.active       = False
            self._physics_body.bounce_count = 0
            # 仅记录按下位置，不立即提交拖拽偏移；清空轨迹队列
            self._press_pos   = event.globalPos()
            self._drag_offset = None
            self._drag_trail.clear()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        """直接接收方向键（焦点在摩托窗口时生效）。"""
        key = event.key()
        if key == Qt.Key_Left:
            self._left_pressed = True
            self._move_dir = -1
            self._flipped = True
            self.update()
            event.accept()
            return
        if key == Qt.Key_Right:
            self._right_pressed = True
            self._move_dir = 1
            self._flipped = False
            self.update()
            event.accept()
            return
        if key == Qt.Key_Up:
            if not event.isAutoRepeat() and not self._up_pressed:
                self._up_pressed = True
                self._request_jump()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        """直接接收方向键释放（焦点在摩托窗口时生效）。"""
        if event.isAutoRepeat():
            event.accept()
            return
        key = event.key()
        if key == Qt.Key_Left:
            self._left_pressed = False
            event.accept()
            return
        if key == Qt.Key_Right:
            self._right_pressed = False
            event.accept()
            return
        if key == Qt.Key_Up:
            self._up_pressed = False
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mouseMoveEvent(self, event):
        """
        左键移动处理（两阶段）：

        阶段一（_drag_offset is None, _press_pos is not None）：
          检测自按下点的移动距离是否 ≥ _DRAG_THRESHOLD。
          未达阈值时忽略移动（保留点击/双击判定）；
          达到阈值时提交拖拽：以原始按下位置为偏移基准（避免窗口跳变），
          取消双击等待，切换为 ClosedHandCursor。

        阶段二（_drag_offset is not None）：
          正常拖拽移动，向轨迹队列追加位置（仅保留最近 100ms）。
        """
        if not (event.buttons() & Qt.LeftButton) or self._fading:
            super().mouseMoveEvent(event)
            return

        if self._drag_offset is not None:
            # 阶段二：已提交拖拽，正常移动并记录轨迹
            new_pos = event.globalPos() - self._drag_offset
            self.move(new_pos)
            now = time.monotonic()
            self._drag_trail.append((now, event.globalPos()))
            cutoff = now - _DRAG_TRAIL_WINDOW_SEC
            while self._drag_trail and self._drag_trail[0][0] < cutoff:
                self._drag_trail.popleft()

        elif self._press_pos is not None:
            # 阶段一：检测是否达到拖拽阈值
            dp      = event.globalPos() - self._press_pos
            dist_sq = dp.x() * dp.x() + dp.y() * dp.y()
            if dist_sq >= _DRAG_THRESHOLD * _DRAG_THRESHOLD:
                # 提交拖拽：以原始按下位置为偏移，确保窗口不跳变
                self._drag_offset         = self._press_pos - self.pos()
                self._pending_click       = False
                self._pending_click_ticks = 0
                self.setCursor(Qt.ClosedHandCursor)
                # 立即移动到当前鼠标位置并记录轨迹起点
                new_pos = event.globalPos() - self._drag_offset
                self.move(new_pos)
                now = time.monotonic()
                self._drag_trail.append((now, event.globalPos()))

    def mouseReleaseEvent(self, event):
        """
        左键释放：
          - 曾进入拖拽（_drag_offset is not None）
              → 从速度轨迹队列计算投掷速度，激活物理体
          - 仅点击未拖拽（_press_pos is not None, _drag_offset is None）
              → 以零速度激活物理（原地自由落体），保留双击判定窗口

        速度来源：轨迹队列首尾之间的 Δpos / Δt（最近 100ms 内 moveEvent 的综合）。
        队列为空（拖拽刚提交即松开）时速度为零。
        """
        if event.button() == Qt.LeftButton and not self._fading:
            if self._drag_offset is not None:
                # ── 拖拽释放：计算投掷速度 ──────────────────────────
                vx, vy = self._compute_release_velocity(event.globalPos())

                vx = max(-_MAX_THROW_VX, min(_MAX_THROW_VX, vx))
                vy = max(-_MAX_THROW_VY, min(_MAX_THROW_VY, vy))

                body        = self._physics_body
                body.x      = float(self.x())
                body.y      = float(self.y())
                body.vx     = vx
                body.vy     = vy
                body.active = True

                self._drag_offset = None
                self._press_pos   = None

            elif self._press_pos is not None:
                # ── 纯点击释放：零速度原地落体 ──────────────────────
                body        = self._physics_body
                body.x      = float(self.x())
                body.y      = float(self.y())
                body.vx     = 0.0
                body.vy     = 0.0
                body.active = True

                self._press_pos = None

            self.setCursor(Qt.OpenHandCursor)

        elif event.button() == Qt.LeftButton:
            # 兜底（淡出中或其他异常路径）
            self._drag_offset = None
            self._press_pos   = None
            self.setCursor(Qt.OpenHandCursor)
        else:
            super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        """绘制当前 QPixmap 到透明背景（支持透明度淡出）。"""
        pixmap = self._get_current_pixmap()
        if pixmap is None or pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setOpacity(self._alpha)
        painter.drawPixmap(self._render_jitter.x(), self._render_jitter.y(), pixmap)

    def closeEvent(self, event):
        """关闭时确保所有事件订阅和物理资源已释放（兜底清理）。"""
        self._event_center.unsubscribe(EventType.TICK,                   self._on_tick_click)
        self._event_center.unsubscribe(EventType.TICK,                   self._tick_fade)
        self._event_center.unsubscribe(EventType.TICK,                   self._on_tick_motion)
        self._event_center.unsubscribe(EventType.TICK,                   self._on_tick_render_jitter)
        self._event_center.unsubscribe(EventType.KEY_PRESS,              self._on_key_press)
        self._event_center.unsubscribe(EventType.KEY_RELEASE,            self._on_key_release)
        self._event_center.unsubscribe(EventType.FRAME,                  self._on_frame_move)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        self._cleanup_physics()
        self._alive = False
        super().closeEvent(event)

