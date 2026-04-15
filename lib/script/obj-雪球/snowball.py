"""单个雪球对象 - 可拖拽投掷、双击淡出、带寿命倒计时的雪球小窗口"""
import random
import time
from collections import deque

from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore    import Qt, QPoint
from PyQt5.QtGui     import QPainter, QPixmap

from config.config                import BEHAVIOR, PHYSICS, SNOWBALL as _SNOWBALL_CFG
from lib.core.event.center        import get_event_center, EventType, Event
from lib.core.clickthrough_state  import is_clickthrough_enabled
from lib.core.physics             import get_physics_world, PhysicsBody
from lib.core.topmost_manager    import get_topmost_manager
from lib.core.voice.snowball_sound import SnowballSound
from lib.core.screen_utils        import get_screen_geometry_for_point


# ── 物理参数（从 PHYSICS 读取，与沙发保持一致）────────────────────────
_MAX_THROW_VX: float     = PHYSICS.get('max_throw_vx', 25.0)
_MAX_THROW_VY: float     = PHYSICS.get('max_throw_vy', 25.0)
_DRAG_THRESHOLD: int     = PHYSICS.get('drag_threshold', 5)
_MAX_BOUNCES: int        = PHYSICS.get('max_bounces', 5)
_FADE_STEP: float        = PHYSICS.get('fade_step', 0.05)
_FADE_INTERVAL_MS: int   = PHYSICS.get('fade_interval_ms', 50)
_GROUND_Y_PCT: float     = PHYSICS.get('ground_y_pct', 0.90)

# 拖拽轨迹参数
_DRAG_TRAIL_WINDOW_SEC: float    = 0.10
_RELEASE_SAMPLE_MIN_DT_SEC: float = 1.0 / 60.0

# 粒子触发概率控制
_PARTICLE_TRIGGER_CHANCE: float = 0.60   # 每次触发的概率（60%）
_PARTICLE_TRIGGER_MAX:    int   = 6      # 单个雪球一生最多触发次数


class Snowball(QWidget):
    """
    单只雪球窗口。

    - 左键按住拖拽：移动雪球到任意位置，松开时继承拖拽速度（可"丢出"）
    - 左键双击：淡出消失
    - 右键：无额外行为
    - 寿命到期自动淡出消失（lifetime_min ~ lifetime_max 秒随机）
    - 物理弹跳：地面为屏幕高度 90%，最多弹跳 max_bounces 次，与左右屏幕边界碰撞
    - 参与管理器层的球间半径检测（暴露 radius / physics_body 属性）
    """

    def __init__(self,
                 pixmap: QPixmap,
                 position: QPoint,
                 size: tuple):
        """
        Args:
            pixmap:   已缩放好的静态 QPixmap
            position: 屏幕全局坐标（左上角）
            size:     窗口尺寸 (width, height)，width == height == diameter
        """
        super().__init__()

        self._pixmap  = pixmap
        self._size    = size
        self._alpha   = 1.0
        self._alive   = True
        self._fading  = False

        # 碰撞半径（供管理器球间碰撞检测使用）
        self.radius: float = size[0] / 2.0

        # 淡出节拍
        self._fade_tick_stride = max(1, int(round(_FADE_INTERVAL_MS / 50.0)))
        self._fade_tick_count  = 0

        # 音效（0.75 衰减）
        self._sound = SnowballSound()

        # 事件中心
        self._event_center = get_event_center()

        # 双击判定
        self._pending_click       = False
        self._pending_click_ticks = 0
        self._double_click_ticks  = BEHAVIOR.get('double_click_ticks', 3)

        # 拖拽状态
        self._press_pos: QPoint | None   = None
        self._drag_offset: QPoint | None = None
        self._drag_trail: deque          = deque()

        # 粒子触发计数（60% 概率，一生最多 _PARTICLE_TRIGGER_MAX 次）
        self._particle_trigger_count: int = 0

        # 冻结状态（静止后冷冻以节约算力，受足够大冲量才重新激活）
        self._frozen: bool = False

        # ── 寿命计时 ────────────────────────────────────────────────
        lt_min_s = _SNOWBALL_CFG.get('lifetime_min', 10)
        lt_max_s = _SNOWBALL_CFG.get('lifetime_max', 15)
        lifetime_s = random.uniform(lt_min_s, lt_max_s)
        # 寿命以 TICK 计数（1 TICK ≈ 50ms）
        self._lifetime_ticks_total = max(1, int(round(lifetime_s * 20.0)))
        self._lifetime_ticks_left  = self._lifetime_ticks_total

        # ── 窗口属性 ─────────────────────────────────────────────────
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
        get_topmost_manager().register(self)

        # ── 物理体 ────────────────────────────────────────────────────
        w, h = size
        spawn_center = QPoint(position.x() + w // 2, position.y() + h // 2)
        screen_geom  = get_screen_geometry_for_point(spawn_center)
        ground_y = screen_geom.y() + screen_geom.height() * _GROUND_Y_PCT - h

        self._physics_body = PhysicsBody(
            x           = float(position.x()),
            y           = float(position.y()),
            ground_y    = ground_y,
            width       = w,
            height      = h,
            max_bounces = _MAX_BOUNCES,
        )
        # 雪球专属地面摩擦系数（比世界默认值更滑）
        self._physics_body.bounce_vx_retain = _SNOWBALL_CFG.get('ground_friction', 0.96)
        self._physics_body.on_position_change = self._on_physics_position_change
        self._physics_body.on_wall_hit        = self._on_physics_wall_hit
        self._physics_body.on_ground_bounce   = self._on_physics_ground_bounce

        self._physics_cleaned = False
        get_physics_world().add_body(self._physics_body)

        # ── 事件订阅 ─────────────────────────────────────────────────
        self._event_center.subscribe(EventType.TICK,                   self._on_tick)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        self.move(position)
        self.show()

        # 召唤后立即激活物理（自然下落到地面）
        self._physics_body.active = True

    # ==================================================================
    # 公开接口
    # ==================================================================

    def get_center(self) -> QPoint:
        """返回雪球圆心的全局屏幕坐标。"""
        return QPoint(
            self.x() + self._size[0] // 2,
            self.y() + self._size[1] // 2,
        )

    def is_alive(self) -> bool:
        return self._alive

    @property
    def physics_body(self) -> PhysicsBody:
        return self._physics_body

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    # ==================================================================
    # 淡出
    # ==================================================================

    def start_fadeout(self):
        """触发淡出消失（幂等）。"""
        if self._fading:
            return
        self._fading              = True
        self._press_pos           = None
        self._drag_offset         = None
        self._pending_click       = False
        self._pending_click_ticks = 0
        self._fade_tick_count     = 0
        self._event_center.unsubscribe(EventType.TICK, self._on_tick)
        self._cleanup_physics()
        self._spawn_snow_particles()
        self._sound.play()
        self._event_center.subscribe(EventType.TICK, self._tick_fade)

    # ==================================================================
    # 物理资源清理
    # ==================================================================

    def _cleanup_physics(self) -> None:
        if self._physics_cleaned:
            return
        self._physics_cleaned     = True
        self._physics_body.active = False
        get_physics_world().remove_body(self._physics_body)

    # ==================================================================
    # 物理回调
    # ==================================================================

    def _on_physics_position_change(self, body: PhysicsBody) -> None:
        if not self._fading and self._drag_offset is None:
            self.move(QPoint(int(body.x), int(body.y)))

    def _on_physics_wall_hit(self, body: PhysicsBody, side: str) -> None:
        self._sound.play()

    def _on_physics_ground_bounce(self, body: PhysicsBody, stopped: bool) -> None:
        self._sound.play()
        self._spawn_snow_drift_particles()
        if stopped:
            self.freeze()

    # ==================================================================
    # 冻结 / 解冻
    # ==================================================================

    def freeze(self) -> None:
        """将雪球冻结：停止物理计算，节约算力（幂等）。"""
        if self._frozen or self._fading or self._drag_offset is not None:
            return
        self._frozen = True
        self._physics_body.active = False

    def unfreeze(self) -> None:
        """解冻雪球：恢复物理计算（幂等）。"""
        if not self._frozen:
            return
        self._frozen = False
        self._physics_body.active = True
        self._physics_body.bounce_count = 0

    # ==================================================================
    # 粒子申请
    # ==================================================================

    def _spawn_snow_particles(self) -> None:
        if self._particle_trigger_count >= _PARTICLE_TRIGGER_MAX:
            return
        if random.random() >= _PARTICLE_TRIGGER_CHANCE:
            return
        self._particle_trigger_count += 1
        center = self.get_center()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'snowball_burst',
            'area_type':   'point',
            'area_data':   (center.x(), center.y()),
        }))

    def _spawn_snow_drift_particles(self) -> None:
        if self._particle_trigger_count >= _PARTICLE_TRIGGER_MAX:
            return
        if random.random() >= _PARTICLE_TRIGGER_CHANCE:
            return
        self._particle_trigger_count += 1
        center = self.get_center()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'snowball_drift',
            'area_type':   'point',
            'area_data':   (center.x(), center.y()),
        }))

    # ==================================================================
    # 拖拽速度计算（与沙发完全一致）
    # ==================================================================

    def _compute_release_velocity(self, release_pos: QPoint) -> tuple[float, float]:
        now = time.monotonic()
        self._drag_trail.append((now, release_pos))

        cutoff = now - _DRAG_TRAIL_WINDOW_SEC
        while self._drag_trail and self._drag_trail[0][0] < cutoff:
            self._drag_trail.popleft()

        if len(self._drag_trail) < 2:
            return 0.0, 0.0

        t1, p1 = self._drag_trail[-1]
        idx    = len(self._drag_trail) - 2
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
    # TICK 回调（双击超时判定 + 寿命倒计时，二合一）
    # ==================================================================

    def _on_tick(self, event: Event):
        # ── 双击超时判定 ────────────────────────────────────────────
        if self._pending_click:
            self._pending_click_ticks += 1
            if self._pending_click_ticks >= self._double_click_ticks:
                self._pending_click       = False
                self._pending_click_ticks = 0

        # ── 寿命倒计时（拖拽中暂停计时，防止拖住后一松手就消失）──────
        if self._drag_offset is None:
            self._lifetime_ticks_left -= 1
            if self._lifetime_ticks_left <= 0:
                self.start_fadeout()

    # ==================================================================
    # Qt 鼠标事件（拖拽逻辑与沙发完全一致）
    # ==================================================================

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._fading:
            if self._pending_click:
                # 双击确认 → 淡出
                self._pending_click       = False
                self._pending_click_ticks = 0
                self._press_pos           = None
                self._drag_offset         = None
                self.start_fadeout()
                return
            self._pending_click       = True
            self._pending_click_ticks = 0
            # 解冻（若已静止冻结，抓起时恢复）
            self._frozen = False
            # 中断物理，重置弹跳计数
            self._physics_body.active       = False
            self._physics_body.bounce_count = 0
            self._press_pos   = event.globalPos()
            self._drag_offset = None
            self._drag_trail.clear()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or self._fading:
            super().mouseMoveEvent(event)
            return

        if self._drag_offset is not None:
            # 阶段二：正常拖拽
            new_pos = event.globalPos() - self._drag_offset
            self.move(new_pos)
            now = time.monotonic()
            self._drag_trail.append((now, event.globalPos()))
            cutoff = now - _DRAG_TRAIL_WINDOW_SEC
            while self._drag_trail and self._drag_trail[0][0] < cutoff:
                self._drag_trail.popleft()

        elif self._press_pos is not None:
            # 阶段一：检测是否超过拖拽阈值
            dp      = event.globalPos() - self._press_pos
            dist_sq = dp.x() * dp.x() + dp.y() * dp.y()
            if dist_sq >= _DRAG_THRESHOLD * _DRAG_THRESHOLD:
                self._drag_offset         = self._press_pos - self.pos()
                self._pending_click       = False
                self._pending_click_ticks = 0
                self.setCursor(Qt.ClosedHandCursor)
                new_pos = event.globalPos() - self._drag_offset
                self.move(new_pos)
                now = time.monotonic()
                self._drag_trail.append((now, event.globalPos()))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and not self._fading:
            if self._drag_offset is not None:
                # 拖拽释放：计算投掷速度，激活物理
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
                # 纯点击释放：零速度原地落体
                body        = self._physics_body
                body.x      = float(self.x())
                body.y      = float(self.y())
                body.vx     = 0.0
                body.vy     = 0.0
                body.active = True

                self._press_pos = None

            self.setCursor(Qt.OpenHandCursor)

        elif event.button() == Qt.LeftButton:
            self._drag_offset = None
            self._press_pos   = None
            self.setCursor(Qt.OpenHandCursor)
        else:
            super().mouseReleaseEvent(event)

    # ==================================================================
    # 淡出节拍
    # ==================================================================

    def _tick_fade(self, event: Event):
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
    # 绘制
    # ==================================================================

    def paintEvent(self, event):
        if self._pixmap is None:
            return
        painter = QPainter(self)
        painter.setOpacity(self._alpha)
        painter.drawPixmap(0, 0, self._pixmap)

    # ==================================================================
    # 关闭兜底
    # ==================================================================

    def closeEvent(self, event):
        self._event_center.unsubscribe(EventType.TICK, self._on_tick)
        self._event_center.unsubscribe(EventType.TICK, self._tick_fade)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        self._cleanup_physics()
        self._alive = False
        super().closeEvent(event)
