"""对象基类模块"""
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap, QImage, QPainter

from config.config import ANIMATION, OBJECTS
from lib.core.layer import Layer
from lib.core.qt_gif_loader import scale_frame
from lib.core.unified_draw import Layer, get_layer_manager
from lib.core.event.center import get_event_center, EventType


def _clamp_opacity(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 1.0
    return max(0.0, min(1.0, number))


def _get_object_opacity_scale() -> float:
    return _clamp_opacity(OBJECTS.get('object_opacity', 1.0))


class GameObject(QWidget):
    """游戏对象基类，提供基本的动画和渲染功能"""

    def __init__(self, gifs: dict, size: tuple[int, int]):
        super().__init__()

        self._gifs = gifs
        self._size = size
        self._frame_idx = 0
        self._alpha = 1.0
        self._current_pix: QPixmap | None = None
        self._flipped = False

        # 渲染器
        from lib.core.render.animation_renderer import AnimationRenderer
        self._renderer = AnimationRenderer(size)

        # 设置窗口属性
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFixedSize(*size)
        get_layer_manager().register(self, Layer.WORLD_OBJECT)

    # ==================================================================
    # 渲染
    # ==================================================================

    def paintEvent(self, event):
        """绘制事件"""
        painter = QPainter(self)
        self._renderer.render(painter, self.rect())

    def _tick_animation(self, state: str = 'idle'):
        """更新动画帧"""
        frames = self._gifs.get(state, [])
        if not frames:
            return

        frame = frames[self._frame_idx % len(frames)]
        self._frame_idx += 1

        # 缩放
        frame = scale_frame(frame, self._size)
        self._current_pix = QPixmap.fromImage(frame)
        self._render()

    def _render(self):
        """使用渲染器进行渲染"""
        if not self._current_pix:
            return

        # 使用渲染器渲染
        self._renderer.set_pixmap(self._current_pix)
        self._renderer.set_alpha(max(0.0, min(1.0, float(self._alpha))) * _get_object_opacity_scale())

    # ==================================================================
    # 生命周期
    # ==================================================================

    def start_animation(self, interval: int = 100, timing_manager=None):
        """启动动画定时器"""
        if timing_manager:
            self._anim_task_id = timing_manager.add_task(
                interval,
                repeat=True
            )
        else:
            self._anim_timer = QTimer(self)
            self._anim_timer.setInterval(interval)
            self._anim_timer.timeout.connect(lambda: self._tick_animation('idle'))
            self._anim_timer.start()

    def stop_animation(self):
        """停止动画定时器"""
        if hasattr(self, '_anim_task_id') and self._anim_task_id:
            if hasattr(self, '_timing_manager') and self._timing_manager:
                self._timing_manager.remove_task(self._anim_task_id)
            self._anim_task_id = None
        if hasattr(self, '_anim_timer'):
            self._anim_timer.stop()

    def start_fadeout(self):
        """启动淡出动画：订阅 TICK 事件（替代独立 QTimer，50ms 间隔完全一致）。"""
        ec = get_event_center()
        ec.subscribe(EventType.TICK, self._tick_fade)

    def _tick_fade(self, event=None):
        """TICK 事件回调（淡出阶段）：逐步降低透明度直至关闭。"""
        self._alpha -= 0.05
        if self._alpha <= 0:
            get_event_center().unsubscribe(EventType.TICK, self._tick_fade)
            self.stop_animation()
            self.close()
        else:
            self.update()
