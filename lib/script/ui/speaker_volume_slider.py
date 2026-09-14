"""音响音量滑条 - 音响右键 UI 中搜索框上方的音量调节控件。

布局与交互：
  - 与「搜索框 + 搜索歌曲」整行等宽，高度与播放进度条一致（20px）
  - 样式完全来自共享滑条视觉（黑/青/粉三层面板 + 竖向矩形手柄）
  - 20 个小刻度，拖动时吸附到 1/20 档，形成颗粒手感
  - 点击或拖动发布 ``MUSIC_VOLUME {'volume': ratio}``，松开时提示当前百分比

定位由 ``SpeakerControlButtons`` 统一管理：滑条贴在搜索框上方，其余按钮整体上移。
"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QPainter

from config.config import UI, SPEAKER_SEARCH_UI
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.event.center import get_event_center, Event, EventType
from lib.core.graphics.media_panel_visuals import (
    PROGRESS_PANEL_HEIGHT,
    SLIDER_TICK_COUNT,
    build_slider_visual,
    slider_ratio_at,
    slider_track_rect,
    snap_slider_ratio,
)
from lib.core.qt_bridge.draw_backend import QtDrawBackend
from lib.core.unified_draw import Layer, get_layer_manager
from lib.script.music import get_music_service


DEFAULT_WIDTH = int(SPEAKER_SEARCH_UI.get('input_width', scale_px(160, min_abs=1))) + int(
    SPEAKER_SEARCH_UI.get('button_width', scale_px(80, min_abs=1))
)
DEFAULT_HEIGHT = PROGRESS_PANEL_HEIGHT


def _music_volume_ratio() -> float | None:
    """读取当前音乐音量（0.0-1.0）；服务不可用时返回 None。"""
    try:
        service = get_music_service()
        return max(0.0, min(1.0, float(service.get_volume_percent()) / 100.0))
    except Exception:
        return None


def snap_ratio(ratio: float) -> float:
    """把任意比例吸附到 1/20 档（滑条颗粒手感）。"""
    return snap_slider_ratio(ratio, SLIDER_TICK_COUNT)


class SpeakerVolumeSlider(QWidget):
    """音响右键 UI 的音量滑条（全局单例的附属控件）。"""

    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(int(width), int(height))
        self.setCursor(Qt.PointingHandCursor)
        get_layer_manager().register(self, Layer.PET_UI)

        self._draw_backend = QtDrawBackend()
        self._event_center = get_event_center()
        self._description = '拖动调节音乐音量'

        self._visible = False
        self._ratio = 0.0
        self._dragging = False
        self._tick_counter = 0

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_anim_finished)

        self._event_center.subscribe(EventType.TICK, self._on_tick)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                     self._on_clickthrough_toggle)

    # ==================================================================
    # 状态
    # ==================================================================

    @property
    def ratio(self) -> float:
        return self._ratio

    @property
    def is_visible(self) -> bool:
        return self._visible

    def track_rect(self):
        return slider_track_rect(width=self.width(), height=self.height())

    def ratio_from_x(self, x: float) -> float:
        """把控件内横坐标换算成 0.0-1.0 的比例。"""
        return slider_ratio_at(self.track_rect(), x)

    def _sync_from_service(self) -> None:
        ratio = _music_volume_ratio()
        if ratio is None:
            return
        snapped = snap_ratio(ratio)
        if abs(snapped - self._ratio) > 1e-9:
            self._ratio = snapped
            self.update()

    def set_ratio(self, ratio: float, *, emit: bool = False, notify: bool = False) -> None:
        snapped = snap_ratio(ratio)
        changed = abs(snapped - self._ratio) > 1e-9
        self._ratio = snapped
        if changed:
            self.update()
        if emit and changed:
            self._event_center.publish(Event(EventType.MUSIC_VOLUME, {'volume': snapped}))
        if notify:
            self._publish_volume_bubble()

    def _publish_volume_bubble(self) -> None:
        ratio = _music_volume_ratio()
        percent = int(round((self._ratio if ratio is None else ratio) * 100))
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'音量 {percent}%',
            'min': 0,
        }))

    # ==================================================================
    # 显示 / 隐藏
    # ==================================================================

    def fade_in(self) -> None:
        if self._visible:
            return
        self._visible = True
        self._sync_from_service()
        self.show()
        self._animate(1.0)

    def fade_out(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._animate(0.0)

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _on_anim_finished(self) -> None:
        if not self._visible:
            self.hide()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents,
                          event.data.get('enabled', False))

    def _on_tick(self, event: Event) -> None:
        if not self._visible or self._dragging:
            return
        self._tick_counter += 1
        if self._tick_counter < 20:
            return
        self._tick_counter = 0
        self._sync_from_service()

    def cleanup(self) -> None:
        try:
            self._event_center.unsubscribe(EventType.TICK, self._on_tick)
            self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                           self._on_clickthrough_toggle)
        except Exception:
            pass
        try:
            self.close()
        except Exception:
            pass

    # ==================================================================
    # 绘制
    # ==================================================================

    def _build_visual(self):
        return build_slider_visual(
            ratio=self._ratio,
            width=self.width(),
            height=self.height(),
            ticks=SLIDER_TICK_COUNT,
            layer=int(Layer.PET_UI),
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        self._draw_backend.render(self._build_visual().batch, painter)
        painter.end()

    # ==================================================================
    # 鼠标交互
    # ==================================================================

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        self._dragging = True
        try:
            self.grabMouse()
        except RuntimeError:
            pass
        self.set_ratio(self.ratio_from_x(event.x()), emit=True)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self.set_ratio(self.ratio_from_x(event.x()), emit=True)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if not self._dragging:
            super().mouseReleaseEvent(event)
            return
        self._dragging = False
        try:
            if self.mouseGrabber() is self:
                self.releaseMouse()
        except RuntimeError:
            pass
        self.set_ratio(self.ratio_from_x(event.x()), emit=True, notify=True)
        super().mouseReleaseEvent(event)


__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "SpeakerVolumeSlider",
    "snap_ratio",
]
