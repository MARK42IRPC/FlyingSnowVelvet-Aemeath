"""关闭按钮类。"""
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.script.ui.close_button_handler import CloseButtonEventHandler
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.visuals import controls
from lib.script.ui import render_bridge
from lib.script.ui.rect_action_button_runtime import (
    RectActionButtonRuntime,
    emit_click_particle,
)


NODE_ID = "close"


class CloseButton:
    """关闭按钮，对齐到输入框右上角上方 4px 处。"""

    WIDTH = scale_px(80, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, on_close):
        self._on_close = on_close
        self._ui_id = 'close_button'
        self._anchor_available = True
        self._event_center = get_event_center()
        self._event_handler = CloseButtonEventHandler(self)
        self._runtime = RectActionButtonRuntime(
            width=self.WIDTH,
            height=self.HEIGHT,
            text="关闭桌宠",
            description=TOOLTIPS["close_button"],
            on_pointer=self._on_pointer,
        )
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._runtime.set_clickthrough(bool(event.data.get("enabled", False)))

    def _on_pointer(self, event):
        emit_click_particle(self._runtime.control, event)
        if event.button == controls.BUTTON_LEFT:
            self.click()
        return controls.PointerClick()

    def click(self):
        if self._on_close:
            self._on_close()

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
