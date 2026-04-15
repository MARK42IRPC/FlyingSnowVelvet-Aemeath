"""单个沙发对象 - 可拖拽投掷、双击淡出、带物理弹跳的沙发小窗口"""
import time
from collections import deque

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore    import Qt, QPoint
from PyQt5.QtGui     import QPainter, QPixmap

from config.config            import BEHAVIOR, PHYSICS
from lib.core.topmost_manager  import get_topmost_manager
from lib.core.event.center     import get_event_center, EventType, Event
from lib.core.clickthrough_state import is_clickthrough_enabled
from lib.core.physics          import get_physics_world, PhysicsBody
from lib.core.particle_utils   import spawn_particle_at_point
from lib.core.screen_utils     import get_screen_geometry_for_point
from lib.core.voice.sofa       import SofaSound


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


class Sofa(QWidget):
    """
    单个沙发窗口。

    - 左键按住拖拽：移动沙发到任意位置，松开时继承拖拽速度（可"丢出"）
    - 左键双击：淡出消失
    - 右键单击：水平镜像翻转
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
        self._flipped        = False
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

        # 事件中心
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.TICK,                   self._on_tick_click)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        # 弹跳音效
        self._sofa_sound = SofaSound()

        # 召唤时立即激活物理体，使其下落
        self._physics_body.active = True

    # ==================================================================
    # 公开接口
    # ==================================================================

    def get_center(self) -> QPoint:
        """返回沙发锚点的全局屏幕坐标（几何中心向上偏移 30px）。"""
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
        """
        碰到屏幕左/右边界时翻转沙发朝向。

        物理世界已将水平速度反向，此处同步图片朝向：
          - 碰左边界 → 速度变为向右 → 翻转（_flipped=True）
          - 碰右边界 → 速度变为向左 → 正向（_flipped=False）
        """
        self._flipped = (side == 'left')
        self.update()
        # 在碰墙接触边的中点生成碰撞粒子
        cx = int(body.x) if side == 'left' else int(body.x + body.width)
        cy = int(body.y + body.height / 2)
        spawn_particle_at_point(cx, cy, 'collision')
        self._sofa_sound.play()

    def _on_physics_ground_bounce(self, body: PhysicsBody, stopped: bool) -> None:
        """触地时在沙发底部中心生成碰撞粒子并播放弹跳音效。"""
        cx = int(body.x + body.width  / 2)
        cy = int(body.y + body.height)
        spawn_particle_at_point(cx, cy, 'collision')
        self._sofa_sound.play()

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

    def _compute_release_velocity(self, release_pos: QPoint) -> tuple[float, float]:
        """
        计算松手瞬时速度：
        1. 在松手时补一帧采样；
        2. 以松手时刻为基准裁剪轨迹窗口；
        3. 使用末段有效采样计算速度。
        """
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
        右键单击 → 翻转图片朝向。
        """
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
        elif event.button() == Qt.RightButton and not self._fading:
            self._flipped = not self._flipped
            self.update()
        else:
            super().mousePressEvent(event)

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
        painter.drawPixmap(0, 0, pixmap)

    def closeEvent(self, event):
        """关闭时确保所有事件订阅和物理资源已释放（兜底清理）。"""
        self._event_center.unsubscribe(EventType.TICK,                   self._on_tick_click)
        self._event_center.unsubscribe(EventType.TICK,                   self._tick_fade)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        self._cleanup_physics()
        self._alive = False
        super().closeEvent(event)
