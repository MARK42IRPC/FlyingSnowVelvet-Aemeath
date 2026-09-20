"""音响响应频段滑条 - 音响右键 UI 右侧的竖向滑条。

布局与交互：
  - 贴在「搜索框 + 搜索歌曲」整行右侧，上下与菜单上半部分对齐
    （顶端对齐第一行按钮，底端对齐搜索框下沿）
  - 外观来自共享竖向滑条视觉（黑/青/粉三层面板 + 单个横向块 + 小刻度）
  - 只有一个块：按住拖动，块的位置就是频段的中心频率，频段固定为中心 ±10Hz，
    中心按 10Hz 吸附，所以拖出来的总是 10Hz 整数倍的区间
  - 松手时提示当前频段

位置由 ``SpeakerControlButtons`` 统一管理；频段本身按世界对象实例存在
``lib.core.speaker_band`` 里，音响每帧按自己的频段取强度，互不影响。
"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QPainter

from config.config import SPEAKER_SEARCH_UI, UI
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.event.center import get_event_center, Event, EventType
from lib.core.graphics.speaker_band_visuals import (
    BAND_SLIDER_WIDTH,
    BandSliderVisual,
    band_hit_test,
    band_ratio_at,
    build_band_slider_visual,
)
from lib.core.graphics.speaker_visuals import SPEAKER_SEARCH_Y
from lib.core.qt_bridge.draw_backend import QtDrawBackend
from lib.core.speaker_band import (
    band_from_center_ratio,
    band_label,
    default_band,
    get_speaker_band,
    set_speaker_band,
)
from lib.core.unified_draw import Layer, get_layer_manager


DEFAULT_WIDTH = BAND_SLIDER_WIDTH
#: 默认高度 = 菜单上半部分（两行按钮 + 音量滑条 + 搜索框）的总高。
DEFAULT_HEIGHT = SPEAKER_SEARCH_Y + int(SPEAKER_SEARCH_UI.get('height', scale_px(36, min_abs=1)))


class SpeakerBandSlider(QWidget):
    """音响右键 UI 的动感响应频段滑条（竖向、单块，全局单例的附属控件）。"""

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
        self._description = TOOLTIPS.get('speaker_band_slider', '拖动调节音响的动感响应频段')

        self._visible = False
        self._speaker = None
        self._band = default_band()
        self._dragging = ''
        self._visual: BandSliderVisual | None = None

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_anim_finished)

        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE,
                                     self._on_clickthrough_toggle)

    # ==================================================================
    # 状态
    # ==================================================================

    @property
    def band(self) -> tuple[float, float]:
        return self._band

    @property
    def is_visible(self) -> bool:
        return self._visible

    @property
    def bound_speaker(self):
        return self._speaker

    def set_speaker(self, speaker) -> None:
        """绑定（或解绑）当前锚定的音响，并同步它的响应频段。"""
        self._speaker = speaker
        self._band = self._read_band()
        self.invalidate()

    def apply_geometry(self, x: int, y: int, height: int) -> None:
        """由按钮组给出的位置与高度（上下对齐菜单上半部分）。"""
        target_height = max(1, int(height))
        if self.height() != target_height:
            self.setFixedSize(self.width(), target_height)
            self._visual = None
        self.move(int(x), int(y))

    def invalidate(self) -> None:
        """频段或尺寸变化后丢弃缓存的绘制批次。"""
        self._visual = None
        self.update()

    def _read_band(self) -> tuple[float, float]:
        speaker = self._speaker
        if speaker is None:
            return default_band()
        try:
            return get_speaker_band(speaker.backend_id, speaker.instance_id)
        except Exception:
            return default_band()

    def _write_band(self) -> None:
        speaker = self._speaker
        if speaker is None:
            return
        try:
            set_speaker_band(
                speaker.backend_id,
                speaker.instance_id,
                self._band[0],
                self._band[1],
            )
        except Exception:
            pass

    def _publish_band_bubble(self) -> None:
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'响应频段 {band_label(self._band)}',
            'min': 0,
        }))

    # ==================================================================
    # 显示 / 隐藏
    # ==================================================================

    def fade_in(self) -> None:
        if self._visible:
            return
        self._visible = True
        self._band = self._read_band()
        self.invalidate()
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

    def cleanup(self) -> None:
        try:
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

    def _build_visual(self) -> BandSliderVisual:
        return build_band_slider_visual(
            band=self._band,
            width=self.width(),
            height=self.height(),
            layer=int(Layer.PET_UI),
        )

    def _ensure_visual(self) -> BandSliderVisual:
        if self._visual is None:
            self._visual = self._build_visual()
        return self._visual

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        self._draw_backend.render(self._ensure_visual().batch, painter)
        painter.end()

    # ==================================================================
    # 鼠标交互
    # ==================================================================

    def _apply_y(self, y: float) -> None:
        """把块拖到指针所在的频率：频段随之变成「中心 ±10Hz」。"""
        if not self._dragging:
            return
        visual = self._ensure_visual()
        ratio = band_ratio_at(visual.track_rect, y)
        band = band_from_center_ratio(ratio)
        if band == self._band:
            return
        self._band = band
        self._write_band()
        self.invalidate()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        visual = self._ensure_visual()
        action = band_hit_test(
            visual.track_rect,
            visual.center_rect,
            event.x(),
            event.y(),
        )
        if not action:
            super().mousePressEvent(event)
            return
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        self._dragging = action
        try:
            self.grabMouse()
        except RuntimeError:
            pass
        self._apply_y(event.y())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._apply_y(event.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if not self._dragging:
            super().mouseReleaseEvent(event)
            return
        self._apply_y(event.y())
        self._dragging = ''
        try:
            if self.mouseGrabber() is self:
                self.releaseMouse()
        except RuntimeError:
            pass
        self._publish_band_bubble()
        super().mouseReleaseEvent(event)


__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "SpeakerBandSlider",
]
