"""单个雪豹对象 - 屏幕上的一只可淡出的雪豹小窗口"""
import random

from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore    import Qt, QPoint
from PyQt5.QtGui     import QPainter, QPixmap

from config.config                import ANIMATION, SNOW_LEOPARD, BEHAVIOR, PHYSICS
from lib.core.qt_gif_loader       import scale_frame
from lib.core.event.center        import get_event_center, EventType, Event
from lib.core.clickthrough_state  import is_clickthrough_enabled
from lib.core.physics             import get_physics_world, PhysicsBody
from lib.core.topmost_manager    import get_topmost_manager
from lib.core.voice.snow          import SnowSound


# ── 从配置文件读取物理参数 ─────────────────────────────────────────────
_JUMP_VX: float = PHYSICS.get('snow_leopard_jump_vx', 5.0)
_JUMP_VY: float = PHYSICS.get('snow_leopard_jump_vy', -13.0)
_FADE_STEP: float = PHYSICS.get('fade_step', 0.05)
_FADE_INTERVAL_MS: int = PHYSICS.get('fade_interval_ms', 50)
_FLIP_INTERVAL: tuple = (
    PHYSICS.get('flip_interval_min', 5000),
    PHYSICS.get('flip_interval_max', 8000),
)


class SnowLeopard(QWidget):
    """
    单只雪豹窗口。

    - 接收预加载的正向/翻转帧列表，自身不再重复解码
    - 默认朝向向左（_flipped=False = 正向/向左）
    - 左键点击 → 向前上方弹跳（最多 3 次落地后停止），每次落地申请 snow 粒子
    - 右键点击 → 翻转朝向（仅静止状态下有效，跳跃中忽略）
    - 每 5~8 秒自动翻转一次朝向（跳跃期间暂停，落地后重新调度）
    - 进入主宠物交互半径后由 SnowLeopardManager 调用 start_fadeout()
    - 消失时在中心位置申请 snow 粒子效果
    - 动画帧率跟随全局 ANIMATION['gif_fps']
    - 集成物理系统：重力、地面弹跳（30% 衰减）、屏幕左右边界碰撞
    """

    # 使用模块级配置变量（已从 PHYSICS 配置读取）

    def __init__(self,
                 frames: list,
                 flipped_frames: list,
                 position: QPoint,
                 size: tuple):
        """
        Args:
            frames:         正向 QImage 帧列表
            flipped_frames: 水平翻转 QImage 帧列表
            position:       屏幕全局坐标（左上角）
            size:           窗口尺寸 (width, height)
        """
        super().__init__()

        self._frames         = frames
        self._flipped_frames = flipped_frames
        self._size           = size
        self._alpha          = 1.0
        self._alive          = True
        self._fading         = False
        self._frame_idx      = 0
        self._fade_tick_stride = max(1, int(round(_FADE_INTERVAL_MS / 50.0)))
        self._fade_tick_count = 0

        # 默认朝向向左（False=正向/向左）
        self._flipped = False

        # 当前待绘制的 QPixmap
        self._current_pixmap: QPixmap | None = None

        # 事件中心（用于申请粒子）
        self._event_center = get_event_center()

        # 落地音效（随机选取 resc/SOUND/snow/ 内的音频）
        self._snow_sound = SnowSound()

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
        self.setCursor(Qt.ArrowCursor)
        get_topmost_manager().register(self)

        # ── 物理体 ────────────────────────────────────────────────
        w, h = size
        self._physics_body = PhysicsBody(
            x           = float(position.x()),
            y           = float(position.y()),
            ground_y    = float(position.y()),  # 地面 = 生成时的 Y（各雪豹独立）
            width       = w,
            height      = h,
            max_bounces = 3,
        )
        self._physics_body.on_position_change = self._on_physics_position_change
        self._physics_body.on_wall_hit        = self._on_physics_wall_hit
        self._physics_body.on_ground_bounce   = self._on_physics_ground_bounce

        # 物理资源是否已清理（防止重复释放）
        self._physics_cleaned = False
        get_physics_world().add_body(self._physics_body)

        # ── 动画：订阅全局 GIF_FRAME 事件（替代独立 QTimer）────────
        self._event_center.subscribe(EventType.GIF_FRAME, self._on_gif_frame)

        # ── 双击判定（与 sofa/speaker 相同的 TICK 计数机制）──────────
        # 待确认的单击状态（True = 有一次左键按下待确认）
        self._pending_click       = False
        # 自首次按下已累积的 tick 数
        self._pending_click_ticks = 0
        # 双击判定间隔（tick 数），读取全局配置，与 sofa/speaker 保持一致
        self._double_click_ticks  = BEHAVIOR.get('double_click_ticks', 3)

        # ── 自动翻转：使用 TimingManager.add_task（替代独立 QTimer）─
        self._flip_task_id = None
        self._event_center.subscribe(EventType.TIMER, self._on_timer_event)
        self._event_center.subscribe(EventType.TICK,  self._on_tick_click)

        # 初始化第一帧，避免首次 paintEvent 时空白
        self._advance_frame()

        # 显示窗口并调度首次翻转
        self.move(position)
        self.show()
        self._schedule_flip()

        # 订阅穿透模式切换，随主宠物同步透传鼠标事件
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    # ==================================================================
    # 公开接口
    # ==================================================================

    def get_center(self) -> QPoint:
        """返回雪豹锚点的全局屏幕坐标（水平居中，垂直中心按配置偏移）。"""
        anchor_offset_y = SNOW_LEOPARD.get('anchor_offset_y', -30)
        return QPoint(
            self.x() + self._size[0] // 2,
            self.y() + self._size[1] // 2 + anchor_offset_y,
        )

    def is_alive(self) -> bool:
        """是否仍然存活（未消亡）。"""
        return self._alive

    def _on_clickthrough_toggle(self, event: Event) -> None:
        """穿透模式开启/关闭时同步自身鼠标透传状态。"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def start_fadeout(self):
        """
        触发淡出消失（幂等，重复调用安全）。

        同时在中心位置申请 snow 粒子效果。
        """
        if self._fading:
            return
        self._fading = True

        # 停止动画订阅、单击判定
        self._event_center.unsubscribe(EventType.GIF_FRAME, self._on_gif_frame)
        self._event_center.unsubscribe(EventType.TICK, self._on_tick_click)
        self._pending_click       = False
        self._pending_click_ticks = 0
        self._fade_tick_count = 0

        # 取消翻转任务
        self._cancel_flip_task()
        self._event_center.unsubscribe(EventType.TIMER, self._on_timer_event)

        # 停止物理模拟并注销
        self._cleanup_physics()

        # 在消失位置申请 snow 粒子与音效
        self._spawn_snow_particles()
        self._snow_sound.play()

        # 启动淡出：订阅 TICK（50ms 间隔）
        self._event_center.subscribe(EventType.TICK, self._tick_fade)

    # ==================================================================
    # 物理资源清理
    # ==================================================================

    def _cleanup_physics(self) -> None:
        """停止物理模拟并从物理世界注销（幂等）。"""
        if self._physics_cleaned:
            return
        self._physics_cleaned = True
        self._physics_body.active = False
        get_physics_world().remove_body(self._physics_body)

    # ==================================================================
    # 翻转逻辑
    # ==================================================================

    def _toggle_flip(self):
        """切换朝向并立即重绘。"""
        self._flipped = not self._flipped
        self._advance_frame()
        self.update()

    def _set_flipped(self, flipped: bool) -> None:
        """设置朝向，仅值变化时刷新帧（避免无效 update）。"""
        if self._flipped != flipped:
            self._flipped = flipped
            self._advance_frame()
            self.update()

    def _cancel_flip_task(self):
        """取消当前待触发的翻转任务（幂等）。"""
        if self._flip_task_id is None:
            return
        from lib.core.timing import get_timing_manager
        tm = get_timing_manager()
        if tm:
            tm.remove_task(self._flip_task_id)
        self._flip_task_id = None

    def _schedule_flip(self):
        """以随机间隔调度下一次自动翻转（正在跳跃或淡出时不调度）。"""
        if self._fading or self._physics_body.active:
            return
        from lib.core.timing import get_timing_manager
        tm = get_timing_manager()
        if not tm:
            return
        interval = random.randint(*_FLIP_INTERVAL)
        self._flip_task_id = tm.add_task(interval, repeat=False)

    def _on_auto_flip(self):
        """自动翻转回调：翻转后重新调度。"""
        if self._fading or self._physics_body.active:
            return
        self._toggle_flip()
        self._schedule_flip()

    def _on_timer_event(self, event: Event):
        """处理 TimingManager 任务触发事件，分发到对应逻辑。"""
        task_id = event.data.get('task_id')
        if task_id == self._flip_task_id:
            self._flip_task_id = None
            self._on_auto_flip()

    # ==================================================================
    # 跳跃
    # ==================================================================

    def spawn_jump(self, power_min: float = 0.8, power_max: float = 1.8) -> None:
        """
        生成时触发初始弹跳，避免雪豹相互重叠。

        由 SnowLeopardManager.spawn_natural() 在创建后立即调用。
        力度倍率随机 [power_min, power_max]，使雪豹散开落点各不相同。
        跳过双击计时判定，直接执行；不播放粒子/音效（出现特效已由管理器处理）。
        生成时随机反转朝向。
        """
        if self._fading:
            return
        # 随机反转朝向
        self._flipped = random.choice([True, False])
        self._advance_frame()
        
        power             = random.uniform(power_min, power_max)
        body              = self._physics_body
        body.bounce_count = 0
        body.vx           = (-_JUMP_VX if not self._flipped else _JUMP_VX) * power
        body.vy           = _JUMP_VY * power
        body.active       = True
        self._cancel_flip_task()

    def _do_jump(self) -> None:
        """
        触发一次跳跃（每次点击均可调用，重置弹跳计数）。

        方向：
          - 朝向左（_flipped=False）→ 向左上方弹出（vx 为负）
          - 朝向右（_flipped=True） → 向右上方弹出（vx 为正）

        力度：在 [jump_power_min, jump_power_max] 范围内随机缩放 vx / vy。
        """
        power = random.uniform(
            SNOW_LEOPARD.get('jump_power_min', 0.8),
            SNOW_LEOPARD.get('jump_power_max', 1.2),
        )
        body              = self._physics_body
        body.bounce_count = 0
        body.vx           = (-_JUMP_VX if not self._flipped else _JUMP_VX) * power
        body.vy           = _JUMP_VY * power
        body.active       = True

        # 跳跃开始时立即发射落雪粒子
        self._spawn_snow_drift_particles()

        # 跳跃期间取消自动翻转任务，落地回调中重新调度
        self._cancel_flip_task()

    # ==================================================================
    # 物理回调
    # ==================================================================

    def _on_physics_position_change(self, body: PhysicsBody) -> None:
        """物理步进后同步窗口位置到新坐标。"""
        if not self._fading:
            self.move(QPoint(int(body.x), int(body.y)))

    def _on_physics_wall_hit(self, body: PhysicsBody, side: str) -> None:
        """
        碰到屏幕左/右边界时翻转精灵朝向。

        物理世界已将水平速度方向反转，此处同步精灵朝向：
          - 碰左边界 → 速度变为正（向右）→ 朝向右（_flipped=True）
          - 碰右边界 → 速度变为负（向左）→ 朝向左（_flipped=False）
        """
        self._set_flipped(side == 'left')

    def _on_physics_ground_bounce(self, body: PhysicsBody, stopped: bool) -> None:
        """触地时申请落雪堆积粒子和落地音效；弹跳序列结束后恢复自动翻转。"""
        self._spawn_snow_drift_particles()
        self._snow_sound.play()
        if stopped:
            self._schedule_flip()

    # ==================================================================
    # 粒子申请
    # ==================================================================

    def _spawn_snow_particles(self) -> None:
        """在雪豹中心位置申请 snow 粒子效果（用于淡出消失）。"""
        center = self.get_center()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'snow',
            'area_type':   'point',
            'area_data':   (center.x(), center.y()),
        }))

    def _spawn_snow_drift_particles(self) -> None:
        """在雪豹中心位置申请 snow_drift 粒子（用于跳跃开始和落地弹跳）。

        粒子从当前位置向下飘落，到达屏幕底部后静止堆积，缓慢消退。
        """
        center = self.get_center()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'snow_drift',
            'area_type':   'point',
            'area_data':   (center.x(), center.y()),
        }))

    def _spawn_burst_line_particles(self) -> None:
        """在雪豹中心位置申请 burst_line 粒子（左键点击时触发）。"""
        center = self.get_center()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'burst_line',
            'area_type':   'point',
            'area_data':   (center.x(), center.y()),
        }))

    # ==================================================================
    # 内部逻辑
    # ==================================================================

    def _advance_frame(self):
        """切换到下一帧，更新 _current_pixmap。"""
        src = self._flipped_frames if self._flipped else self._frames
        if not src:
            return
        raw = src[self._frame_idx % len(src)]
        self._frame_idx += 1
        scaled = scale_frame(raw, self._size)
        self._current_pixmap = QPixmap.fromImage(scaled)

    def _on_gif_frame(self, event: Event):
        """GIF_FRAME 事件回调：推进帧并重绘（替代独立 _anim_timer）。"""
        self._advance_frame()
        self.update()

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
    # Qt 事件
    # ==================================================================

    def _on_tick_click(self, event: Event):
        """
        TICK 事件回调：双击超时判定。

        与 sofa/speaker._on_tick_click 逻辑完全一致：
        每 TICK 计数加一，达到 double_click_ticks 则超时，确认为单击，触发弹跳。
        """
        if not self._pending_click:
            return
        self._pending_click_ticks += 1
        if self._pending_click_ticks >= self._double_click_ticks:
            self._pending_click       = False
            self._pending_click_ticks = 0
            self._on_click_confirmed()

    def mousePressEvent(self, event):
        """
        左键点击 → 双击判定：
          · 间隔内首次按下：等待确认，超时后执行弹跳
          · 间隔内再次按下：取消等待，立即淡出消失
        右键点击 → 翻转朝向（仅静止状态有效）+ 音效。
        """
        if event.button() == Qt.LeftButton and not self._fading:
            if self._pending_click:
                # 双击间隔内二次点击 → 双击确认，取消跳跃，直接淡出
                self._pending_click       = False
                self._pending_click_ticks = 0
                self.start_fadeout()
            else:
                # 首次点击 → 等待双击超时
                self._pending_click       = True
                self._pending_click_ticks = 0
        elif (event.button() == Qt.RightButton
              and not self._fading
              and not self._physics_body.active):
            # 右键：翻转朝向（跳跃期间忽略，避免干扰物理方向）
            self._toggle_flip()
            self._cancel_flip_task()
            self._schedule_flip()
            self._snow_sound.play()
        else:
            super().mousePressEvent(event)

    def _on_click_confirmed(self):
        """单击确认回调（双击间隔超时）→ 执行弹跳。"""
        if self._fading:
            return
        self._do_jump()
        self._spawn_burst_line_particles()
        self._snow_sound.play()

    def paintEvent(self, event):
        """自定义绘制：直接画到透明背景上。"""
        if self._current_pixmap is None:
            return
        painter = QPainter(self)
        painter.setOpacity(self._alpha)
        painter.drawPixmap(0, 0, self._current_pixmap)

    def closeEvent(self, event):
        """窗口关闭时确保所有事件订阅和物理资源已释放（兜底清理）。"""
        # 兜底：start_fadeout 正常路径已取消，此处防止外部直接 close 时残留订阅
        self._event_center.unsubscribe(EventType.GIF_FRAME, self._on_gif_frame)
        self._event_center.unsubscribe(EventType.TICK, self._on_tick_click)
        self._event_center.unsubscribe(EventType.TICK, self._tick_fade)
        self._event_center.unsubscribe(EventType.TIMER, self._on_timer_event)
        self._cancel_flip_task()
        self._cleanup_physics()
        super().closeEvent(event)
