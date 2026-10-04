"""语音聊天模式切换按钮。"""
from config.scale import scale_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.visuals import controls
from lib.script.ui import render_bridge
from lib.script.ui.rect_action_button_runtime import (
    RectActionButtonRuntime,
    emit_click_particle,
)


NODE_ID = "chat_mode"


class ChatModeButton:
    """语音模式/文字模式切换按钮。"""

    WIDTH = scale_px(80, min_abs=80)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, launch_wuwa_button=None):
        self._launch_button = launch_wuwa_button
        self._listening = False
        self._ui_id = 'chat_mode_button'
        self._event_center = get_event_center()
        self._runtime = RectActionButtonRuntime(
            width=self.WIDTH,
            height=self.HEIGHT,
            text="文字模式",
            description="点击切换语音/文字模式",
            on_pointer=self._on_pointer,
        )
        self._event_center.subscribe(EventType.MIC_STT_STATE_CHANGE, self._on_stt_state_change)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def _text(self):
        return "语音模式" if self._listening else "文字模式"

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._runtime.set_clickthrough(bool(event.data.get("enabled", False)))

    def _on_stt_state_change(self, event: Event) -> None:
        listening = bool(event.data.get("is_listening"))
        if self._listening != listening:
            self._listening = listening
            self._runtime.control.text = self._text()
            self._runtime.update()

    def _on_pointer(self, event):
        emit_click_particle(self._runtime.control, event)
        if event.button != controls.BUTTON_LEFT:
            return controls.PointerClick()
        if self._listening:
            self._event_center.publish(Event(EventType.MIC_STT_STOP, {
                "source": "chat_mode_button",
            }))
        else:
            self._event_center.publish(Event(EventType.MIC_STT_START, {
                "source": "chat_mode_button",
                "auto_mode": False,
                "auto_submit": True,
                "emit_partial": True,
            }))
        return controls.PointerClick()

    # ── 视图（宿主转发）───────────────────────────────────────────
    @property
    def _visible(self):
        return self._runtime.control.visible

    @_visible.setter
    def _visible(self, value):
        self._runtime.control.visible = bool(value)

    def width(self):
        return self._runtime.width_()

    def height(self):
        return self._runtime.height_()

    def x(self):
        return self._runtime.x()

    def y(self):
        return self._runtime.y()

    def isVisible(self):
        return self._runtime.is_visible()

    def update(self):
        self._runtime.update()

    def move(self, x, y):
        self._runtime.host.move(int(x), int(y))

    def hide(self):
        self._runtime.hide()

    def close(self):
        self._runtime.cleanup()

    def fade_in(self):
        self._runtime.fade_in()

    def fade_out(self):
        self._runtime.fade_out_with_particle(self._event_center)

    def _update_position(self):
        rect = render_bridge.family_placement(self._runtime.host, NODE_ID)
        if rect is None:
            return
        render_bridge.move_widget_to_global(
            self._runtime.host, int(rect.x), int(rect.y)
        )
