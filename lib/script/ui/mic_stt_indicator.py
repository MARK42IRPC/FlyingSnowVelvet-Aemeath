"""语音识别状态指示器。"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QRect
from PyQt5.QtGui import QPainter, QColor, QCursor
import time

from config.config import COLORS, UI
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.unified_draw import Layer, get_layer_manager
from lib.core.screen_utils import clamp_rect_position
from lib.core.anchor_utils import apply_ui_opacity


class MicSttIndicator(QWidget):
    """监听语音识别状态的小方块，点击可停止监听。"""

    SIZE = scale_px(24, min_abs=18)

    def __init__(self, pet_window):
        super().__init__()
        self._pet_window = pet_window
        self._event_center = get_event_center()
        self._visible = False
        self._listening = False
        self._speech_active = False
        self._margin = scale_px(4, min_abs=3)
        self._extra_offset_y = scale_px(20, min_abs=20)
        self._description = "语音识别中，点击可停止"
        self._hover_radius = scale_px(120, min_abs=90)
        self._hide_delay = 2.0
        self._last_mouse_inside_ts = time.monotonic()

        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.SIZE, self.SIZE)
        get_layer_manager().register(self, Layer.PET_UI)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_anim_finished)

        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.MIC_STT_STATE_CHANGE, self._on_state_change)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

        self.hide()

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _on_frame(self, event: Event) -> None:
        if self._listening:
            self._update_position()
            self._update_visibility_by_cursor()

    def _on_state_change(self, event: Event) -> None:
        listening = bool(event.data.get('is_listening'))
        self._speech_active = bool(event.data.get('speech_active'))
        status = str(event.data.get('status', '') or '').strip()
        if status:
            self._description = f"语音识别({status})，点击停止"

        if listening:
            self._listening = True
            self._update_position()
            self._last_mouse_inside_ts = time.monotonic()
            self._show_indicator()
            self.update()
        else:
            self._listening = False
            self._hide_indicator()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get('enabled', False))

    def _on_anim_finished(self) -> None:
        if not self._visible:
            self.hide()

    # ------------------------------------------------------------------
    # 可视化
    # ------------------------------------------------------------------
    def _show_indicator(self) -> None:
        if self._visible:
            return
        self._visible = True
        self.show()
        self._animate(1.0)

    def _hide_indicator(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._animate(0.0)

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _update_position(self) -> None:
        if self._pet_window is None:
            return
        geom = self._pet_window.geometry()
        x = geom.x() + self._margin
        y = geom.y() - self.height() - self._margin + self._extra_offset_y
        x, y, _ = clamp_rect_position(
            x,
            y,
            self.width(),
            self.height(),
            point=geom.topLeft(),
            fallback_widget=self._pet_window,
        )
        self.move(x, y)
        if not self._visible:
            # ensure visibility check uses latest geometry
            self._last_geom_update = time.monotonic()

    def _update_visibility_by_cursor(self) -> None:
        cursor_pos = QCursor.pos()
        geom = self.geometry()
        center_x = geom.center().x()
        center_y = geom.center().y()
        dx = cursor_pos.x() - center_x
        dy = cursor_pos.y() - center_y
        distance_sq = dx * dx + dy * dy
        inside = distance_sq <= (self._hover_radius ** 2)

        now = time.monotonic()
        if inside:
            self._last_mouse_inside_ts = now
            if self._listening:
                self._show_indicator()
        else:
            if self._visible and (now - self._last_mouse_inside_ts) >= self._hide_delay:
                self._hide_indicator()

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        layer = scale_px(2, min_abs=1)
        cyan_rect = self.rect().adjusted(layer, layer, -layer, -layer)
        content_rect = cyan_rect.adjusted(layer, layer, -layer, -layer)

        painter.fillRect(self.rect(), COLORS['black'])
        painter.fillRect(cyan_rect, COLORS['cyan'])

        fill_color = QColor(255, 230, 240) if self._speech_active else COLORS['pink']
        painter.fillRect(content_rect, fill_color)

        # 麦克风图标
        painter.setRenderHint(QPainter.Antialiasing, True)
        mic_width = max(4, content_rect.width() // 3)
        mic_height = max(6, content_rect.height() - scale_px(4, min_abs=2))
        mic_x = content_rect.center().x() - mic_width // 2
        mic_y = content_rect.top() + scale_px(1, min_abs=1)
        mic_rect = QRect(mic_x, mic_y, mic_width, mic_height).intersected(content_rect)
        painter.setBrush(COLORS.get('deep_blue', COLORS['black']))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(mic_rect, 2, 2)

        stem_height = scale_px(3, min_abs=2)
        stem_width = max(2, mic_width // 2)
        stem_rect = QRect(
            mic_rect.center().x() - stem_width // 2,
            mic_rect.bottom() - stem_height + 1,
            stem_width,
            stem_height
        )
        painter.drawRect(stem_rect)
        base_height = scale_px(1, min_abs=1)
        base_rect = QRect(
            stem_rect.center().x() - stem_width,
            stem_rect.bottom() + 1,
            stem_width * 2,
            base_height
        )
        painter.drawRect(base_rect)

        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._event_center.publish(Event(EventType.MIC_STT_STOP, {
            'source': 'mic_stt_indicator',
        }))

    def closeEvent(self, event):
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._event_center.unsubscribe(EventType.MIC_STT_STATE_CHANGE, self._on_state_change)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        super().closeEvent(event)
