"""启动鸣潮按钮 - 检测并启动鸣潮（统一走 wuwa_launcher 的重活）。"""
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.visuals import controls
from lib.script.app.wuwa_launcher import get_wuthering_waves_launcher
from lib.script.ui import render_bridge
from lib.script.ui.rect_action_button_runtime import (
    RectActionButtonRuntime,
    emit_click_particle,
)


NODE_ID = "launch_wuwa"


class LaunchWutheringWavesButton:
    """启动鸣潮按钮，左锚点对齐到鼠标穿透按钮的左锚点。"""

    WIDTH = scale_px(80, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, clickthrough_button=None):
        self._clickthrough_button = clickthrough_button
        self._ui_id = 'launch_wuwa_button'
        self._event_center = get_event_center()
        self._runtime = RectActionButtonRuntime(
            width=self.WIDTH,
            height=self.HEIGHT,
            text="启动鸣潮",
            description=TOOLTIPS.get("launch_wuwa_button", "检测并启动鸣潮"),
            on_pointer=self._on_pointer,
        )
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self._runtime.set_clickthrough(bool(event.data.get("enabled", False)))

    def _launch_wuthering_waves(self) -> None:
        get_wuthering_waves_launcher().launch()

    def _on_pointer(self, event):
        emit_click_particle(self._runtime.control, event)
        if event.button == controls.BUTTON_LEFT:
            self._launch_wuthering_waves()
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
