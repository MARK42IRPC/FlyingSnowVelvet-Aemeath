"""单个雪堆对象 - 只能水平拖动、支持批次生成雪豹、双击淡出的雪堆小窗口"""
import random
from typing import Callable, Optional

from PyQt5.QtWidgets import QWidget, QApplication
from PyQt5.QtCore    import Qt, QPoint
from PyQt5.QtGui     import QPainter, QPixmap

from lib.core.topmost_manager import get_topmost_manager
from lib.core.event.center    import get_event_center, EventType, Event
from lib.core.clickthrough_state import is_clickthrough_enabled
from lib.core.screen_utils    import get_screen_geometry_for_point
from lib.core.voice.snow      import SnowSound
from config.config            import PHYSICS, BEHAVIOR


# 从配置文件读取物理参数
_FADE_STEP: float = PHYSICS.get('fade_step', 0.05)
_FADE_INTERVAL_MS: int = PHYSICS.get('fade_interval_ms', 50)


class SnowPile(QWidget):
    """
    单个雪堆窗口。

    交互行为：
    - 左键按住拖拽：仅允许水平移动，垂直位置锁定，屏幕边缘限制
    - 左键/右键点击：发出 snow 音效 + snow_drift 粒子（雪豹同款落雪粒子）
    - 右键点击：额外通知 Manager 生成一只雪豹（受自然数量上限约束）
    - 左键双击：额外触发淡出消失
    - 定时批次生成：每隔 10~20 秒触发一批（1~2 只），批次内间隔 3~5 秒
    """

    # 使用模块级配置变量（已从 PHYSICS 配置读取）

    def __init__(self,
                 pixmap: QPixmap,
                 position: QPoint,
                 size: tuple,
                 spawn_callback: Callable,
                 config: dict):
        """
        Args:
            pixmap:          已随机缩放至目标尺寸的 QPixmap
            position:        屏幕全局坐标（左上角）
            size:            窗口尺寸 (width, height)
            spawn_callback:  callable(pile: SnowPile) -> None，请求在此位置生成一只雪豹
            config:          SNOW_PILE 配置字典（用于批次参数读取）
        """
        super().__init__()

        self._pixmap   = pixmap
        self._size     = size
        self._spawn_cb = spawn_callback
        self._cfg      = config
        self._alive    = True
        self._fading   = False
        self._alpha    = 1.0
        self._fade_tick_stride = max(1, int(round(_FADE_INTERVAL_MS / 50.0)))
        self._fade_tick_count = 0

        # 拖拽时记录鼠标相对窗口左上角的水平偏移
        self._drag_offset_x: Optional[int] = None

        # 记录初始所属屏幕，用于多屏拖拽边界
        self._screen_geom = get_screen_geometry_for_point(position)

        # snow 音效（随机选取 resc/SOUND/snow/ 内的音频）
        self._snow_sound = SnowSound()

        # 事件中心
        self._event_center = get_event_center()

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
        self.setCursor(Qt.SizeHorCursor)

        self.move(position)
        self.show()
        get_topmost_manager().register(self)

        # 订阅穿透模式切换
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                     self._on_clickthrough_toggle)

        # ── 批次生成：使用 TimingManager.add_task（替代两个独立 QTimer）
        self._batch_task_id: Optional[str] = None
        self._batch_item_task_id: Optional[str] = None
        self._batch_remaining: int = 0
        self._event_center.subscribe(EventType.TIMER, self._on_timer_event)

        # ── 双击判定（与 sofa/speaker 相同的 TICK 计数机制）──────────
        self._pending_click       = False
        self._pending_click_ticks = 0
        self._double_click_ticks  = BEHAVIOR.get('double_click_ticks', 3)
        self._event_center.subscribe(EventType.TICK, self._on_tick_click)

        self._schedule_next_batch()

    # ==================================================================
    # 公开接口
    # ==================================================================

    def get_center(self) -> QPoint:
        """返回雪堆中心的全局屏幕坐标。"""
        return QPoint(
            self.x() + self._size[0] // 2,
            self.y() + self._size[1] // 2,
        )

    def is_alive(self) -> bool:
        return self._alive

    def start_fadeout(self):
        """触发淡出消失（幂等，重复调用安全）。"""
        if self._fading:
            return
        self._fading              = True
        self._pending_click       = False
        self._pending_click_ticks = 0
        self._fade_tick_count = 0

        # 取消双击判定订阅、批次任务并退订 TIMER
        self._event_center.unsubscribe(EventType.TICK, self._on_tick_click)
        self._cancel_batch_tasks()
        self._event_center.unsubscribe(EventType.TIMER, self._on_timer_event)

        # 启动淡出：订阅 TICK（50ms 间隔）
        self._event_center.subscribe(EventType.TICK, self._tick_fade)

    # ==================================================================
    # 批次任务管理
    # ==================================================================

    def _cancel_batch_tasks(self):
        """取消所有待触发的批次任务（幂等）。"""
        from lib.core.timing import get_timing_manager
        tm = get_timing_manager()
        if tm:
            if self._batch_task_id:
                tm.remove_task(self._batch_task_id)
            if self._batch_item_task_id:
                tm.remove_task(self._batch_item_task_id)
        self._batch_task_id = None
        self._batch_item_task_id = None

    def _on_timer_event(self, event: Event):
        """处理 TimingManager 任务触发事件，分发批次逻辑。"""
        task_id = event.data.get('task_id')
        if task_id == self._batch_task_id:
            self._batch_task_id = None
            self._start_batch()
        elif task_id == self._batch_item_task_id:
            self._batch_item_task_id = None
            self._spawn_next_in_batch()

    def _on_tick_click(self, event: Event):
        """
        TICK 事件回调：双击超时判定。

        与 sofa/speaker._on_tick_click 逻辑完全一致：
        每 TICK 计数加一，达到 double_click_ticks 则超时，确认为单击（无额外行为）。
        """
        if not self._pending_click:
            return
        self._pending_click_ticks += 1
        if self._pending_click_ticks >= self._double_click_ticks:
            self._pending_click       = False
            self._pending_click_ticks = 0

    # ==================================================================
    # 批次生成
    # ==================================================================

    def _schedule_next_batch(self):
        """调度下一批次（10~20 秒后触发）。"""
        if self._fading:
            return
        from lib.core.timing import get_timing_manager
        tm = get_timing_manager()
        if not tm:
            return
        lo, hi = self._cfg.get('batch_interval', (10000, 20000))
        self._batch_task_id = tm.add_task(random.randint(lo, hi), repeat=False)

    def _start_batch(self):
        """批次开始：确定本批数量，立即生成第一只。"""
        if self._fading:
            return
        lo, hi = self._cfg.get('batch_size', (1, 2))
        self._batch_remaining = random.randint(lo, hi)
        self._spawn_next_in_batch()

    def _spawn_next_in_batch(self):
        """生成批次内下一只；批次耗尽后调度下一批。"""
        if self._fading:
            return
        if self._batch_remaining <= 0:
            self._schedule_next_batch()
            return

        self._batch_remaining -= 1
        self._spawn_cb(self)  # 通知 Manager 在此雪堆位置生成一只雪豹

        if self._batch_remaining > 0:
            from lib.core.timing import get_timing_manager
            tm = get_timing_manager()
            if not tm:
                return
            lo, hi = self._cfg.get('batch_item_interval', (3000, 5000))
            self._batch_item_task_id = tm.add_task(random.randint(lo, hi), repeat=False)
        else:
            self._schedule_next_batch()

    # ==================================================================
    # 粒子申请
    # ==================================================================

    def _spawn_snow_drift_particles(self) -> None:
        """在雪堆中心申请 snow_drift 粒子（雪豹同款落雪粒子）。"""
        center = self.get_center()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'snow_drift',
            'area_type':   'point',
            'area_data':   (center.x(), center.y()),
        }))

    # ==================================================================
    # 淡出
    # ==================================================================

    def _tick_fade(self, event: Event):
        """TICK 事件回调（淡出阶段）：逐步降低透明度，归零后关闭窗口。"""
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
    # 事件响应
    # ==================================================================

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    # ==================================================================
    # Qt 事件
    # ==================================================================

    def mousePressEvent(self, event):
        """
        左键：
          · 双击判定间隔内（_pending_click=True）再次按下 → snow 音效 + snow_drift 粒子 + 淡出消失
          · 首次按下（_pending_click=False）→ 进入待确认状态 + 开始水平拖拽 + snow 音效 + snow_drift 粒子
        右键：snow 音效 + snow_drift 粒子 + 通知生成一只雪豹。
        """
        if event.button() == Qt.LeftButton and not self._fading:
            if self._pending_click:
                # 双击间隔内二次按下 → 双击确认 → 淡出
                self._pending_click       = False
                self._pending_click_ticks = 0
                self._drag_offset_x       = None
                self.setCursor(Qt.SizeHorCursor)
                self._snow_sound.play()
                self._spawn_snow_drift_particles()
                self.start_fadeout()
                return
            # 首次按下：进入待确认状态 + 开始拖拽
            self._pending_click       = True
            self._pending_click_ticks = 0
            self._drag_offset_x = event.pos().x()
            self.setCursor(Qt.ClosedHandCursor)
            self._snow_sound.play()
            self._spawn_snow_drift_particles()
        elif event.button() == Qt.RightButton and not self._fading:
            self._snow_sound.play()
            self._spawn_snow_drift_particles()
            self._spawn_cb(self)  # 右键：直接生成一只雪豹
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """左键拖拽 → 仅更新水平坐标，Y 轴保持不变。"""
        if (event.buttons() & Qt.LeftButton) and self._drag_offset_x is not None:
            self._screen_geom = get_screen_geometry_for_point(event.globalPos())
            new_x = event.globalPos().x() - self._drag_offset_x
            min_x = self._screen_geom.x()
            max_x = self._screen_geom.x() + self._screen_geom.width() - self._size[0]
            if max_x < min_x:
                max_x = min_x
            new_x = max(min_x, min(new_x, max_x))
            self.move(new_x, self.y())
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """左键释放 → 清除拖拽状态，恢复光标。"""
        if event.button() == Qt.LeftButton:
            self._drag_offset_x = None
            self.setCursor(Qt.SizeHorCursor)
        else:
            super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        """绘制 QPixmap 到透明背景（带透明度支持）。"""
        if self._pixmap is None or self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setOpacity(self._alpha)
        painter.drawPixmap(0, 0, self._pixmap)

    def closeEvent(self, event):
        """窗口关闭时确保所有事件订阅和任务已清理（兜底）。"""
        self._cancel_batch_tasks()
        self._event_center.unsubscribe(EventType.TICK, self._on_tick_click)
        self._event_center.unsubscribe(EventType.TIMER, self._on_timer_event)
        self._event_center.unsubscribe(EventType.TICK, self._tick_fade)
        self._alive = False
        super().closeEvent(event)
