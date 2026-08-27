"""Companion/office routing mode button for the pet command UI."""

from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QWidget

from config.config import UI
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.qt_bridge.font import get_ui_font
from lib.core.qt_bridge.screen import clamp_rect_position
from lib.core.unified_draw import Layer, get_layer_manager
from lib.script.ui.rect_action_button_style import paint_rect_action_button


class InteractionModeButton(QWidget):
    """Toggle ordinary input between companion chat and office tasks."""

    WIDTH = scale_px(80, min_abs=80)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, chat_mode_button=None):
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        get_layer_manager().register(self, Layer.PET_UI)

        self._anchor_button = chat_mode_button
        self._visible = False
        self._hovered = False
        self._mode = "companion"
        self._description = "切换陪伴模式与办公模式"

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(UI["ui_fade_duration"])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._font = get_ui_font()
        self._font.setBold(True)
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        self._event_center.subscribe(EventType.INTERACTION_MODE_CHANGED, self._on_mode_changed)

    def _text(self) -> str:
        return "办公模式" if self._mode == "office" else "陪伴模式"

    def _on_frame(self, event: Event) -> None:
        del event
        if self._visible:
            self._update_position()

    def _on_anchor_response(self, event: Event) -> None:
        if self._visible and (event.data or {}).get("ui_id") in ("all", "chat_mode_button"):
            self._update_position()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool((event.data or {}).get("enabled")))

    def _on_mode_changed(self, event: Event) -> None:
        mode = str((event.data or {}).get("mode", ""))
        if mode in {"companion", "office"} and mode != self._mode:
            self._mode = mode
            self.update()

    def _update_position(self) -> None:
        button = self._anchor_button
        if button is None or not button.isVisible():
            return
        target_right_x = button.x() + button.width()
        target_right_y = button.y() + button.height() // 2
        x, y, _ = clamp_rect_position(
            target_right_x,
            target_right_y - self.HEIGHT // 2,
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
            "particle_id": "right_fade",
            "area_type": "rect",
            "area_data": (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()),
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

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        paint_rect_action_button(
            painter,
            self.rect(),
            self._font,
            self._text(),
            hovered=self._hovered,
        )

    def mousePressEvent(self, event) -> None:
        from lib.script.ui._particle_helper import publish_click_particle

        publish_click_particle(self, event)
        if event.button() == Qt.LeftButton:
            self._event_center.publish(Event(EventType.INTERACTION_MODE_SET, {
                "toggle": True,
                "source": "interaction_mode_button",
            }))

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def closeEvent(self, event) -> None:
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._event_center.unsubscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        self._event_center.unsubscribe(EventType.INTERACTION_MODE_CHANGED, self._on_mode_changed)
        super().closeEvent(event)
