"""右键 UI 更多功能按钮。"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint
from PyQt5.QtGui import QPainter

from config.config import UI
from config.font_config import get_ui_font
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.unified_draw import Layer, get_layer_manager
from lib.core.screen_utils import clamp_rect_position
from lib.core.anchor_utils import apply_ui_opacity
from lib.script.ui.rect_action_button_style import paint_rect_action_button


class MoreFunctionsButton(QWidget):
    """更多功能按钮，左锚点对齐到“文字模式”按钮的右锚点。"""

    WIDTH = scale_px(80, min_abs=80)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, chat_mode_button=None):
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

        self._chat_mode_button = chat_mode_button
        self._visible = False
        self._hovered = False
        self._description = TOOLTIPS.get('more_functions_button', '打开系统托盘更多功能菜单')

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._font = get_ui_font()
        self._font.setBold(True)

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def _on_frame(self, event: Event) -> None:
        if self._visible:
            self._update_position()

    def _on_anchor_response(self, event: Event) -> None:
        if not self._visible:
            return
        ui_id = event.data.get('ui_id')
        if ui_id in ('all', 'chat_mode_button'):
            self._update_position()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get('enabled', False))

    def _update_position(self) -> None:
        if not self._chat_mode_button or not self._chat_mode_button.isVisible():
            return
        btn_x = self._chat_mode_button.x()
        btn_y = self._chat_mode_button.y()
        btn_width = self._chat_mode_button.width()
        btn_height = self._chat_mode_button.height()
        target_right_x = btn_x + btn_width
        target_right_y = btn_y + btn_height // 2
        new_x = target_right_x
        new_y = target_right_y - self.HEIGHT // 2
        x, y, _ = clamp_rect_position(
            new_x,
            new_y,
            self.WIDTH,
            self.HEIGHT,
            point=QPoint(target_right_x, target_right_y),
            fallback_widget=self,
        )
        if self.x() != x or self.y() != y:
            self.move(x, y)

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
        rect = self.geometry()
        self._anim.finished.connect(self._on_fade_out_complete)
        self._animate(0.0)
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
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

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_rect_action_button(painter, self.rect(), self._font, '更多功能', hovered=self._hovered)

    def mousePressEvent(self, event):
        from lib.script.ui._particle_helper import publish_click_particle

        publish_click_particle(self, event)
        if event.button() != Qt.LeftButton:
            return

        try:
            from lib.core.qt_bridge.tray_icon import get_tray_icon

            tray = get_tray_icon()
            if tray is None or not tray.initialize():
                raise RuntimeError('系统托盘不可用')
            tray.show_context_menu()
        except Exception as e:
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': f'打开更多功能失败: {e}',
                'min': 12,
                'max': 120,
            }))

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def closeEvent(self, event):
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._event_center.unsubscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        super().closeEvent(event)
