"""单个音响对象 - 可拖拽投掷、双击淡出、带物理弹跳的音响小窗口

当前版本为沙发换皮，音响专属功能（播放控制等）待后续添加。
"""
import time
from collections import deque

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore    import Qt, QPoint, QTimer
from PyQt5.QtGui     import QPainter, QPixmap, QTransform

from config.config           import BEHAVIOR, PHYSICS, SPEAKER_AUDIO
from lib.core.topmost_manager import get_topmost_manager
from lib.core.event.center    import get_event_center, EventType, Event
from lib.core.clickthrough_state import is_clickthrough_enabled
from lib.core.physics         import get_physics_world, PhysicsBody
from lib.core.particle_utils  import spawn_particle_at_point
from lib.core.voice.sofa      import SofaSound


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

# 从配置文件读取音频可视化参数
_SCALE_RANGE: float = SPEAKER_AUDIO.get('scale_range', 0.1)
_SCALE_EXP: float = SPEAKER_AUDIO.get('scale_exp', 2.0)
_EMA_ATTACK: float = SPEAKER_AUDIO.get('ema_attack', 0.35)
_EMA_DECAY: float = SPEAKER_AUDIO.get('ema_decay', 0.08)
_FREQ_MIN: float = SPEAKER_AUDIO.get('freq_min', 200.0)
_FREQ_MAX: float = SPEAKER_AUDIO.get('freq_max', 2000.0)


class Speaker(QWidget):
    """
    单个音响窗口。

    - 左键按住拖拽：移动音响到任意位置，松开时继承拖拽速度（可"丢出"）
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

        # 拖拽 / 点击判定状态
        self._press_pos: QPoint | None   = None
        self._drag_offset: QPoint | None = None
        self._drag_trail: deque          = deque()

        # 本地淡出定时器（避免受全局 TICK 暂停影响导致“假死”）
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(max(1, int(_FADE_INTERVAL_MS)))
        self._fade_timer.timeout.connect(self._tick_fade)

        # 频率强度（EMA 平滑后的频率响应，0.0–1.0）
        self._freq_intensity: float = 0.0

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
        sh       = QApplication.primaryScreen().geometry().height()
        ground_y = sh * _GROUND_Y_PCT - h

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

        # ── 双击判定 ──────────────────────────────────────────────
        self._pending_click       = False
        self._pending_click_ticks = 0
        self._double_click_ticks  = BEHAVIOR.get('double_click_ticks', 3)

        self.move(position)
        self.show()
        get_topmost_manager().register(self)

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.TICK,                   self._on_tick_click)
        self._event_center.subscribe(EventType.FRAME,                  self._on_frame_frequency)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        # 弹跳音效（复用沙发音效）
        self._sofa_sound = SofaSound()

        # 根据初始位置设定翻转方向
        self._update_flip()

        # 召唤时立即激活物理体，使其下落
        self._physics_body.active = True

    # ==================================================================
    # 公开接口
    # ==================================================================

    def get_center(self) -> QPoint:
        """返回音响锚点的全局屏幕坐标（几何中心向上偏移 30px）。"""
        return QPoint(
            self.x() + self._size[0] // 2,
            self.y() + self._size[1] // 2 - 30,
        )

    def is_alive(self) -> bool:
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
        self._event_center.unsubscribe(EventType.TICK,  self._on_tick_click)
        self._event_center.unsubscribe(EventType.FRAME,                  self._on_frame_frequency)
        self._cleanup_physics()
        if not self._fade_timer.isActive():
            self._fade_timer.start()

    def _tick_fade(self, event: Event | None = None):
        step = _FADE_STEP if _FADE_STEP > 0 else 0.05
        self._alpha -= step
        if self._alpha <= 0.0:
            self._alpha = 0.0
            self._fade_timer.stop()
            self._alive = False
            self.close()
        else:
            self.update()

    # ==================================================================
    # 物理资源
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
        if not self._fading:
            self.move(QPoint(int(body.x), int(body.y)))
            self._update_flip()

    def _on_physics_wall_hit(self, body: PhysicsBody, side: str) -> None:
        self._update_flip()
        cx = int(body.x) if side == 'left' else int(body.x + body.width)
        cy = int(body.y + body.height / 2)
        spawn_particle_at_point(cx, cy, 'white_pink_collision')
        self._sofa_sound.play()

    def _on_physics_ground_bounce(self, body: PhysicsBody, stopped: bool) -> None:
        cx = int(body.x + body.width  / 2)
        cy = int(body.y + body.height)
        spawn_particle_at_point(cx, cy, 'white_pink_collision')
        self._sofa_sound.play()

    # ==================================================================
    # 双击判定
    # ==================================================================

    def _on_tick_click(self, event: Event):
        if not self._pending_click:
            return
        self._pending_click_ticks += 1
        if self._pending_click_ticks >= self._double_click_ticks:
            self._pending_click       = False
            self._pending_click_ticks = 0

    # ==================================================================
    # 响度缩放
    # ==================================================================

    def _on_frame_frequency(self, event: Event) -> None:
        """每帧采样系统音频频率，EMA 平滑后触发重绘。"""
        from lib.core.audio_meter import get_audio_meter
        freq_intensity = get_audio_meter().get_frequency_intensity()  # 0.0–1.0 频率强度
        if freq_intensity is None:
            freq_intensity = 0.0
        # 非对称 EMA：上升快（attack），下降慢（decay）
        alpha = _EMA_ATTACK if freq_intensity > self._freq_intensity else _EMA_DECAY
        self._freq_intensity = alpha * freq_intensity + (1.0 - alpha) * self._freq_intensity
        self.update()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get('enabled', False))

    # ==================================================================
    # 内部辅助
    # ==================================================================

    def _get_current_pixmap(self) -> QPixmap:
        return self._flipped_pixmap if self._flipped else self._pixmap

    def _update_flip(self) -> None:
        """根据当前位置更新翻转状态：中心点在屏幕右半侧时翻转，左半侧不翻转。"""
        sw          = QApplication.primaryScreen().geometry().width()
        new_flipped = (self.x() + self._size[0] // 2) >= sw // 2
        if new_flipped != self._flipped:
            self._flipped = new_flipped
            self.update()

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
        if event.button() == Qt.LeftButton and not self._fading:
            if self._pending_click:
                self._pending_click       = False
                self._pending_click_ticks = 0
                self._press_pos           = None
                self._drag_offset         = None
                self.start_fadeout()
                return
            self._pending_click       = True
            self._pending_click_ticks = 0
            self._physics_body.active       = False
            self._physics_body.bounce_count = 0
            self._press_pos   = event.globalPos()
            self._drag_offset = None
            self._drag_trail.clear()
        elif event.button() == Qt.RightButton and not self._fading:
            # 右键：切换音响专属搜索 UI
            from lib.script.ui.speaker_search_dialog import get_speaker_search_dialog
            dlg = get_speaker_search_dialog()
            if dlg is not None:
                dlg.toggle(self)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or self._fading:
            super().mouseMoveEvent(event)
            return

        if self._drag_offset is not None:
            new_pos = event.globalPos() - self._drag_offset
            self.move(new_pos)
            self._update_flip()
            now = time.monotonic()
            self._drag_trail.append((now, event.globalPos()))
            cutoff = now - _DRAG_TRAIL_WINDOW_SEC
            while self._drag_trail and self._drag_trail[0][0] < cutoff:
                self._drag_trail.popleft()

        elif self._press_pos is not None:
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

    def paintEvent(self, event):
        pixmap = self._get_current_pixmap()
        if pixmap is None or pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setOpacity(self._alpha)

        # 以窗口中心为轴：水平正缩放，垂直负缩放（压扁效果随频率响应变化）
        amp = self._freq_intensity ** _SCALE_EXP      # 指数映射，低频抑制，高频放大
        sx = 1.0 + amp * _SCALE_RANGE   # 水平：1.0 → 1.05
        sy = 1.0 - amp * _SCALE_RANGE   # 垂直：1.0 → 0.95
        if abs(sx - 1.0) > 1e-4 or abs(sy - 1.0) > 1e-4:
            cx, cy = self._size[0] / 2.0, self._size[1] / 2.0
            transform = QTransform()
            transform.translate(cx, cy)
            transform.scale(sx, sy)
            transform.translate(-cx, -cy)
            painter.setTransform(transform)

        painter.drawPixmap(0, 0, pixmap)

    def closeEvent(self, event):
        self._event_center.unsubscribe(EventType.TICK,                   self._on_tick_click)
        self._event_center.unsubscribe(EventType.FRAME,                  self._on_frame_frequency)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        self._fade_timer.stop()
        self._cleanup_physics()
        self._alive = False
        super().closeEvent(event)
