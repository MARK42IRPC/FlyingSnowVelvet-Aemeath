"""鼠标穿透按钮类。"""
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.visuals import controls
from lib.script.voice.ams_clickthrough_reminder import AmsClickthroughReminderSound
from lib.script.ui import render_bridge
from lib.script.ui.rect_action_button_runtime import RectActionButtonRuntime
from lib.script.ui._particle_helper import publish_click_particle_at


NODE_ID = "clickthrough"


class ClickThroughButton:
    """鼠标穿透按钮：左键开启穿透、提示并关闭命令框。"""

    WIDTH = scale_px(80, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, on_click=None):
        self._on_click = on_click
        self._clickthrough_enabled = False
        self._ui_id = 'clickthrough_button'
        self._event_center = get_event_center()
        self._clickthrough_reminder_sound = AmsClickthroughReminderSound(
            interruptible=False
        )
        self._runtime = RectActionButtonRuntime(
            width=self.WIDTH,
            height=self.HEIGHT,
            text="鼠标穿透",
            description=TOOLTIPS["clickthrough_button"],
            on_pointer=self._on_pointer,
        )
        self._event_center.subscribe(
            EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle
        )

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._runtime.set_clickthrough(bool(event.data.get("enabled", False)))

    def click(self):
        self._clickthrough_enabled = True
        self._clickthrough_reminder_sound.play()
        self._event_center.publish(
            Event(EventType.UI_CLICKTHROUGH_TOGGLE, {"enabled": True})
        )
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": "鼠标穿透已开启", "min": 0, "max": 60,
        }))
        self._event_center.publish(Event(EventType.UI_COMMAND_TOGGLE, {"entity": None}))
        if self._on_click:
            self._on_click(True)

    def _on_pointer(self, event):
        particle_id = self._runtime.control.click_particle_id(event)
        if particle_id:
            publish_click_particle_at(
                particle_id, int(event.screen.x), int(event.screen.y)
            )
        if event.button == controls.BUTTON_LEFT:
            self.click()
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
