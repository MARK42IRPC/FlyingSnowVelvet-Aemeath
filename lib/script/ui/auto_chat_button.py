"""自动语聊按钮。"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QPoint

from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.screen_utils import clamp_rect_position
from lib.script.ui.rect_action_button_style import RectActionButton


class AutoChatButton(RectActionButton):
    """右键 UI 的自动语聊开关按钮。"""

    WIDTH = scale_px(80, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, launch_wuwa_button=None):
        super().__init__(self.WIDTH, self.HEIGHT, TOOLTIPS.get('auto_chat_button', '开启或关闭自动语聊'))

        self._launch_wuwa_button = launch_wuwa_button
        self._enabled = False
        self._ui_id = 'auto_chat_button'
        self._target_ui_id = 'launch_wuwa_button'

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        self._event_center.subscribe(EventType.MIC_STT_STATE_CHANGE, self._on_stt_state_change)

    def _button_text(self) -> str:
        return '自动语聊' if self._enabled else '语聊关闭'

    def _on_frame(self, event):
        if self._visible:
            self._update_position()

    def _on_anchor_response(self, event):
        if not self._visible:
            return

        ui_id = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        if ui_id == self._ui_id and window_id == self._target_ui_id:
            self._update_position()
        elif ui_id == 'all' and window_id == self._target_ui_id and anchor_id == 'all':
            self._update_position()

    def _update_position(self):
        if not self._launch_wuwa_button:
            return

        btn_x = self._launch_wuwa_button.x()
        btn_y = self._launch_wuwa_button.y()
        btn_w = self._launch_wuwa_button.width()
        btn_h = self._launch_wuwa_button.height()

        new_x = btn_x + btn_w
        new_y = btn_y

        x, y, _ = clamp_rect_position(
            new_x,
            new_y,
            self.WIDTH,
            self.HEIGHT,
            point=QPoint(btn_x + btn_w, btn_y),
            fallback_widget=self,
        )

        if self.x() != x or self.y() != y:
            self.move(x, y)

    def fade_in(self):
        if self._visible:
            return
        self._visible = True
        self.show()
        self._update_position()
        self._animate(1.0)

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False

        rect = self.geometry()
        self._anim.finished.connect(self._on_fade_out_complete)
        self._animate(0.0)

        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
        }))

    def _on_fade_out_complete(self):
        try:
            self._anim.finished.disconnect(self._on_fade_out_complete)
        except TypeError:
            pass
        self.hide()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get('enabled', False))

    def _on_stt_state_change(self, event: Event) -> None:
        enabled = bool(event.data.get('auto_mode', False))
        if self._enabled != enabled:
            self._enabled = enabled
            self.update()

    def closeEvent(self, event):
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._event_center.unsubscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        self._event_center.unsubscribe(EventType.MIC_STT_STATE_CHANGE, self._on_stt_state_change)
        super().closeEvent(event)

    def mousePressEvent(self, event):
        from lib.script.ui._particle_helper import publish_click_particle

        publish_click_particle(self, event)
        if event.button() != Qt.LeftButton:
            return

        if self._enabled:
            self._event_center.publish(Event(EventType.MIC_STT_STOP, {
                'source': 'auto_chat_button',
            }))
        else:
            self._event_center.publish(Event(EventType.MIC_STT_START, {
                'source': 'auto_chat_button',
                'auto_mode': True,
                'auto_submit': True,
                'emit_partial': True,
            }))
