"""计时器管理模块 - 统一管理所有定时任务，通过事件系统发布事件"""
from PyQt5.QtCore import QTimer, QObject
from typing import Optional
import time
import uuid

from lib.core.event.center import get_event_center, EventType, Event
from lib.core.logger import get_logger
logger = get_logger(__name__)


class Task:
    """定时任务类"""

    def __init__(self, task_id: str, interval_ticks: int, repeat: bool = True):
        self.id = task_id
        self.interval_ticks = interval_ticks
        self.repeat = repeat
        self.current_tick = 0
        self.active = True


class TimingManager(QObject):
    """
    统一计时器管理器
    
    三个独立的定时器，完全解耦：
    - Tick定时器：20 tick/秒（每50ms触发一次tick事件）
    - Frame定时器：60fps（每16.67ms触发一次frame事件，用于窗口移动）
    - GIF帧定时器：10fps（每100ms触发一次GIF帧事件，用于动画播放）
    
    通过事件中心发布帧事件、tick事件、GIF帧事件和定时器事件
    """

    # 硬编码：20 tick/秒 = 50ms/tick
    TICKS_PER_SECOND = 20
    TICK_INTERVAL_MS = 50

    def __init__(self, frame_fps: int = 60, gif_fps: int = 10):
        super().__init__()

        # ── 三个独立的定时器 ────────────────────────────────────────
        
        # Tick定时器：每50ms触发一次（20tick/秒）
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_interval_ms = self.TICK_INTERVAL_MS
        self._tick_count = 0
        self._last_tick_monotonic = time.monotonic()

        # Frame定时器：每16.67ms触发一次（60fps）
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._on_frame)
        self._frame_interval_ms = 1000 // frame_fps
        self._frame_count = 0
        self._last_frame_monotonic = time.monotonic()

        # GIF帧定时器：每100ms触发一次（10fps）
        self._gif_timer = QTimer(self)
        self._gif_timer.timeout.connect(self._on_gif_frame)
        self._gif_interval_ms = 1000 // gif_fps
        self._gif_frame_count = 0

        # 任务列表
        self._tasks: dict[str, Task] = {}
        self._task_pause_sources: set[str] = set()
        self._tasks_paused = False
        self._running = False

        # 事件中心
        self._event_center = get_event_center()

        # 订阅计时器暂停/恢复事件
        self._event_center.subscribe(EventType.TIMER_PAUSE, self._on_timer_pause)
        self._event_center.subscribe(EventType.TIMER_RESUME, self._on_timer_resume)

    def start(self):
        """启动所有定时器"""
        if not self._running:
            self._running = True
            self._tick_timer.start(self._tick_interval_ms)
            self._frame_timer.start(self._frame_interval_ms)
            self._gif_timer.start(self._gif_interval_ms)
            logger.info("[TimingManager] Started: Tick(%sms), Frame(%sms), GIF(%sms)",
                        self._tick_interval_ms, self._frame_interval_ms, self._gif_interval_ms)

    def stop(self):
        """停止所有定时器"""
        if self._running:
            self._running = False
            self._tick_timer.stop()
            self._frame_timer.stop()
            self._gif_timer.stop()

    def _on_tick(self):
        """Tick定时器回调：发布tick事件，更新所有任务"""
        now = time.monotonic()
        dt_ms = max(0.0, (now - self._last_tick_monotonic) * 1000.0)
        self._last_tick_monotonic = now
        self._tick_count += 1
        
        # 发布tick事件（每50ms一次，20次/秒）
        tick_event = Event(EventType.TICK, {
            'tick_count': self._tick_count,
            'dt_ms': dt_ms,
            'timestamp': now,
        })
        self._event_center.publish(tick_event)

        if self._tasks_paused:
            return

        # 更新所有任务
        for task_id, task in list(self._tasks.items()):
            if not task.active:
                continue

            task.current_tick += 1

            if task.current_tick >= task.interval_ticks:
                task.current_tick = 0

                # 发布定时器事件
                timer_event = Event(EventType.TIMER, {
                    'task_id': task_id,
                    'repeat': task.repeat
                })
                self._event_center.publish(timer_event)

                if not task.repeat:
                    self.remove_task(task_id)

    def _on_frame(self):
        """Frame定时器回调：发布frame事件（用于窗口移动）"""
        now = time.monotonic()
        frame_dt_ms = max(0.0, (now - self._last_frame_monotonic) * 1000.0)
        self._last_frame_monotonic = now
        self._frame_count += 1
        tick_elapsed_ms = max(0.0, (now - self._last_tick_monotonic) * 1000.0)
        alpha = min(1.0, tick_elapsed_ms / max(1.0, float(self._tick_interval_ms)))
        frame_event = Event(EventType.FRAME, {
            'frame_count': self._frame_count,
            'dt_ms': frame_dt_ms,
            'tick_alpha': alpha,
            'timestamp': now,
        })
        self._event_center.publish(frame_event)

    def _on_gif_frame(self):
        """GIF帧定时器回调：发布GIF帧事件（用于动画播放）"""
        self._gif_frame_count += 1
        gif_frame_event = Event(EventType.GIF_FRAME, {
            'frame_count': self._gif_frame_count
        })
        self._event_center.publish(gif_frame_event)

    def add_task(self, interval_ms: int, repeat: bool = True) -> str:
        """
        添加定时任务（基于tick）

        Args:
            interval_ms: 间隔时间（毫秒）
            repeat: 是否重复执行

        Returns:
            任务ID
        """
        # 将毫秒转换为tick数（20tick/秒 = 50ms/tick）
        interval_ticks = max(1, int(interval_ms / self.TICK_INTERVAL_MS))
        task_id = str(uuid.uuid4())
        task = Task(task_id, interval_ticks, repeat)
        self._tasks[task_id] = task
        return task_id

    def remove_task(self, task_id: str):
        """移除任务"""
        if task_id in self._tasks:
            self._tasks[task_id].active = False
            del self._tasks[task_id]

    def pause_task(self, task_id: str):
        """暂停任务（保留当前进度）"""
        if task_id in self._tasks:
            task = self._tasks[task_id]
            task.active = False
            logger.debug("[TimingManager] Paused task %s: current_tick=%s/%s",
                         task_id, task.current_tick, task.interval_ticks)

    def resume_task(self, task_id: str):
        """恢复任务（从暂停时的进度继续）"""
        if task_id in self._tasks:
            task = self._tasks[task_id]
            # 恢复时保留 current_tick 进度，从暂停处继续计时
            task.active = True
            logger.debug("[TimingManager] Resumed task %s: current_tick=%s/%s",
                         task_id, task.current_tick, task.interval_ticks)

    def clear_all(self):
        """清空所有任务"""
        self._tasks.clear()

    def set_gif_fps(self, fps: int):
        """
        设置GIF帧率

        Args:
            fps: 每秒帧数（默认10）
        """
        self._gif_interval_ms = 1000 // max(1, min(60, fps))
        if self._running:
            self._gif_timer.setInterval(self._gif_interval_ms)
        logger.debug("[TimingManager] GIF fps set to %s (%sms)", fps, self._gif_interval_ms)

    def get_gif_fps(self) -> int:
        """获取当前GIF帧率"""
        return 1000 // self._gif_interval_ms

    def set_frame_fps(self, fps: int):
        """
        设置全局帧率

        Args:
            fps: 每秒帧数（默认60）
        """
        self._frame_interval_ms = 1000 // max(1, min(120, fps))
        if self._running:
            self._frame_timer.setInterval(self._frame_interval_ms)
        logger.debug("[TimingManager] Frame fps set to %s (%sms)", fps, self._frame_interval_ms)

    def get_frame_fps(self) -> int:
        """获取当前帧率"""
        return 1000 // self._frame_interval_ms

    def _on_timer_pause(self, event: Event):
        """处理计时器暂停事件（仅暂停任务，不暂停全局 tick）。"""
        source = (event.data or {}).get('source') or 'default'
        source = str(source)
        if source not in self._task_pause_sources:
            self._task_pause_sources.add(source)
        self._tasks_paused = bool(self._task_pause_sources)
        logger.debug(
            "[TimingManager] Task timers paused by source=%s (active_pausers=%s)",
            source,
            len(self._task_pause_sources),
        )

    def _on_timer_resume(self, event: Event):
        """处理计时器恢复事件（仅恢复任务，不影响全局 tick）。"""
        source = (event.data or {}).get('source') or 'default'
        source = str(source)
        if source in self._task_pause_sources:
            self._task_pause_sources.remove(source)
        self._tasks_paused = bool(self._task_pause_sources)
        logger.debug(
            "[TimingManager] Task timers resume by source=%s (active_pausers=%s)",
            source,
            len(self._task_pause_sources),
        )
