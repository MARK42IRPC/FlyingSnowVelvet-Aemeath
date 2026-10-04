"""缩放按钮类 - 放大/缩小桌宠。"""
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.desktop_actions import adjust_desktop_scale
from lib.core.render.visuals import controls
from config.user_scale_config import get_user_scale_config
from lib.script.ui import render_bridge
from lib.script.ui.rect_action_button_runtime import (
    RectActionButtonRuntime,
    emit_click_particle,
)


class ScaleUpButton:
    """放大按钮，左锚点对齐到鼠标穿透按钮的右锚点。"""

    WIDTH = scale_px(40, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)
    SCALE_DELTA = 0.1
    NODE_ID = "scale_up"

    def __init__(self, clickthrough_button=None):
        self._clickthrough_button = clickthrough_button
        self._scale_config = get_user_scale_config()
        self._ui_id = 'scale_up_button'
        self._event_center = get_event_center()
        self._runtime = RectActionButtonRuntime(
            width=self.WIDTH,
            height=self.HEIGHT,
            text="+",
            description=TOOLTIPS.get("scale_up_button", "放大桌宠（重启生效）"),
            on_pointer=self._on_pointer,
        )
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._runtime.set_clickthrough(bool(event.data.get("enabled", False)))

    def click(self):
        adjust_desktop_scale(self.SCALE_DELTA)
        self._runtime.update()

    def _on_pointer(self, event):
        emit_click_particle(self._runtime.control, event)
        if event.button == controls.BUTTON_LEFT:
            self.click()
        return controls.PointerClick()

    def _update_position(self):
        rect = render_bridge.family_placement(self._runtime.host, self.NODE_ID)
        if rect is None:
            return
        render_bridge.move_widget_to_global(
            self._runtime.host, int(rect.x), int(rect.y)
        )

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


class ScaleDownButton:
    """缩小按钮，左锚点对齐到放大按钮的右锚点。"""

    WIDTH = scale_px(40, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)
    SCALE_DELTA = -0.1
    NODE_ID = "scale_down"

    def __init__(self, scale_up_button=None):
        self._scale_up_button = scale_up_button
        self._scale_config = get_user_scale_config()
        self._ui_id = 'scale_down_button'
        self._event_center = get_event_center()
        self._runtime = RectActionButtonRuntime(
            width=self.WIDTH,
            height=self.HEIGHT,
            text="-",
            description=TOOLTIPS.get("scale_down_button", "缩小桌宠（重启生效）"),
            on_pointer=self._on_pointer,
        )
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._runtime.set_clickthrough(bool(event.data.get("enabled", False)))

    def click(self):
        adjust_desktop_scale(self.SCALE_DELTA)
        self._runtime.update()

    def _on_pointer(self, event):
        emit_click_particle(self._runtime.control, event)
        if event.button == controls.BUTTON_LEFT:
            self.click()
        return controls.PointerClick()

    def _update_position(self):
        rect = render_bridge.family_placement(self._runtime.host, self.NODE_ID)
        if rect is None:
            return
        render_bridge.move_widget_to_global(
            self._runtime.host, int(rect.x), int(rect.y)
        )

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
