"""语音聊天模式切换按钮。"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QPainter

from config.config import UI
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.unified_draw import Layer, get_layer_manager
from lib.core.anchor_utils import apply_ui_opacity
from lib.script.ui.rect_action_button_style import paint_rect_action_button
from lib.script.ui.render_bridge import family_placement, move_widget_to_global, ui_font as get_ui_font, widget_global_rect


class ChatModeButton(QWidget):
    """聊天模式/文字模式切换按钮，控制 Vosk 监听。"""

    WIDTH = scale_px(80, min_abs=80)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, launch_wuwa_button=None):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        get_layer_manager().register(self, Layer.PET_UI)

        self._launch_button = launch_wuwa_button
        self._visible = False
        self._listening = False
        self._description = "点击切换语音/文字模式"
        self._hovered = False

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._font = get_ui_font()
        self._font.setBold(True)

        self._ui_id = 'chat_mode_button'
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        self._event_center.subscribe(EventType.MIC_STT_STATE_CHANGE, self._on_stt_state_change)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def _text(self) -> str:
        return '聊天模式' if self._listening else '文字模式'

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _on_frame(self, event: Event) -> None:
        if self._visible:
            self._update_position()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get('enabled', False))

    def _on_stt_state_change(self, event: Event) -> None:
        listening = bool(event.data.get('is_listening'))
        if self._listening != listening:
            self._listening = listening
            self.update()

    def _update_position(self):
        """更新窗口位置 - 由 RightClickUiLayer 的锚点图给出整族矩形。"""
        rect = family_placement(self, 'chat_mode')
        if rect is None:
            return
        move_widget_to_global(self, int(rect.x), int(rect.y))

    def fade_in(self) -> None:
        if self._visible:
            return
        self._visible = True
        self.show()
        self._update_position()
        self._animate(1.0)

    def fade_out(self) -> None:
        if not self._visible:
            return
        self._visible = False
        try:
            self._anim.finished.disconnect(self._on_fade_out_complete)
        except TypeError:
            pass
        rect = widget_global_rect(self)
        self._anim.finished.connect(self._on_fade_out_complete)
        self._animate(0.0)
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (int(rect.x), int(rect.y), int(rect.x) + int(rect.width), int(rect.y) + int(rect.height))
        }))

    def _on_fade_out_complete(self) -> None:
        try:
            self._anim.finished.disconnect(self._on_fade_out_complete)
        except TypeError:
            pass
        self.hide()
        self._anim.stop()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    # ------------------------------------------------------------------
    # QWidget
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        paint_rect_action_button(painter, self.rect(), self._font, self._text(), hovered=self._hovered)

    def mousePressEvent(self, event):
        from lib.script.ui._particle_helper import publish_click_particle

        publish_click_particle(self, event)
        if event.button() != Qt.LeftButton:
            return

        if self._listening:
            self._event_center.publish(Event(EventType.MIC_STT_STOP, {
                'source': 'chat_mode_button',
            }))
        else:
            self._event_center.publish(Event(EventType.MIC_STT_START, {
                'source': 'chat_mode_button',
                'auto_mode': False,
                'auto_submit': True,
                'emit_partial': True,
            }))

    def closeEvent(self, event):
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        self._event_center.unsubscribe(EventType.MIC_STT_STATE_CHANGE, self._on_stt_state_change)
        super().closeEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)
