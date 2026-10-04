"""恢复穿透按钮类。

在穿透模式下按鼠标距离动态调整透明度，上中锚点对齐到 pet_window 的下中锚点。
按钮始终存在，鼠标靠近时逐渐显示、远离时逐渐透明。
"""
from config.config import ANIMATION
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.render.visuals import controls
from lib.core.render.visuals.types import Point, coerce_point
from lib.script.ui import render_bridge
from lib.script.ui.rect_action_button_runtime import (
    RectActionButtonRuntime,
    emit_click_particle,
)


class RestoreButton:
    """恢复穿透按钮。"""

    WIDTH = scale_px(80, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)

    def __init__(self, pet_widget=None):
        self._ui_id = 'restore_button'
        self._target_ui_id = 'pet_window'
        self._anchor_point = None
        self._anchor_available = True
        self._clickthrough_enabled = False
        self._mouse_pos = None
        self._proximity_threshold = scale_px(100, min_abs=1)
        self._fade_distance = scale_px(50, min_abs=1)
        self._event_center = get_event_center()
        self._runtime = RectActionButtonRuntime(
            width=self.WIDTH,
            height=self.HEIGHT,
            text="恢复穿透",
            description=TOOLTIPS["restore_button"],
            on_pointer=self._on_pointer,
            on_clickthrough=self._on_clickthrough_state,
        )

        if pet_widget is not None:
            pet_width = ANIMATION["pet_size"][0]
            pet_height = ANIMATION["pet_size"][1]
            pet_pos = pet_widget.get_core_position()
            self._anchor_point = Point(
                int(pet_pos.x) + pet_width // 2,
                int(pet_pos.y) + pet_height,
            )
            self._update_position()
            self._event_center.publish(Event(EventType.UI_CREATE, {
                "window_id": self._target_ui_id,
                "anchor_id": "bottom",
                "ui_id": self._ui_id,
            }))

        self._runtime.fade_in()
        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.MOUSE_MOVE, self._on_mouse_move)
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CREATE, self._on_ui_create)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    # ── 锚点 / 位置 ───────────────────────────────────────────────
    def _on_frame(self, event):
        if self._anchor_available and self._anchor_point:
            self._update_position()
        self._refresh_proximity()

    def _refresh_proximity(self):
        if self._anchor_point is None:
            self._runtime.set_direct_opacity(1.0)
            return
        if self._mouse_pos is None:
            return
        distance = ((self._mouse_pos.x - self._anchor_point.x) ** 2
                    + (self._mouse_pos.y - self._anchor_point.y) ** 2) ** 0.5
        if distance <= self._proximity_threshold:
            self._runtime.set_direct_opacity(1.0)
        else:
            fade_span = max(1.0, float(self._fade_distance))
            ratio = max(0.0, min(1.0, 1.0 - (distance - self._proximity_threshold) / fade_span))
            self._runtime.set_direct_opacity(ratio)

    def _on_mouse_move(self, event):
        point = coerce_point(event.data.get("global_pos"))
        self._mouse_pos = (
            None if point is None
            else Point(int(round(point.x)), int(round(point.y)))
        )

    def _on_anchor_response(self, event):
        pet_width = ANIMATION["pet_size"][0]
        pet_height = ANIMATION["pet_size"][1]
        if event.data.get("ui_id") == self._ui_id:
            point = coerce_point(event.data.get("anchor_point"))
            if point is None:
                return
            self._anchor_point = Point(
                point.x - pet_width // 2,
                point.y - pet_height,
            )
            self._update_position()
        elif event.data.get("ui_id") == "all" and event.data.get("window_id") == self._target_ui_id:
            if event.data.get("anchor_id") == "all":
                pet_pos = coerce_point(event.data.get("anchor_point"))
                if pet_pos is None:
                    return
                new_point = Point(pet_pos.x + pet_width // 2, pet_pos.y + pet_height)
                if self._anchor_point != new_point:
                    self._anchor_point = new_point
                    self._update_position()

    def _on_ui_create(self, event):
        if event.data.get("ui_id") == self._ui_id:
            self._event_center.publish(Event(EventType.UI_ANCHOR_RESPONSE, {
                "window_id": self._ui_id,
                "anchor_id": event.data.get("anchor_id"),
                "anchor_point": self._anchor_point,
                "ui_id": self._ui_id,
            }))

    def _on_clickthrough_toggle(self, event):
        self._clickthrough_enabled = bool(event.data.get("enabled", False))
        if self._clickthrough_enabled:
            self._anchor_point = None

    def _on_clickthrough_state(self, enabled):
        self._clickthrough_enabled = bool(enabled)
        if self._clickthrough_enabled:
            self._anchor_point = None

    def _update_position(self):
        if not self._anchor_point:
            return
        placement = render_bridge.place_at_point(
            (self.WIDTH, self.HEIGHT),
            self._anchor_point,
            render_bridge.screen_rect_for_point(
                point=self._anchor_point, fallback_widget=self._runtime.host
            ),
            target_anchor_id="bottom",
            self_anchor_id="top",
            offset_y=scale_px(4, min_abs=1),
        )
        render_bridge.move_widget_to_global(
            self._runtime.host, int(placement.x), int(placement.y)
        )

    # ── 点击 ──────────────────────────────────────────────────────
    def click(self):
        self._event_center.publish(Event(EventType.UI_CLICKTHROUGH_TOGGLE, {"enabled": False}))
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": "鼠标穿透已关闭", "min": 0, "max": 60,
        }))
        self.fade_out()

    def _on_pointer(self, event):
        emit_click_particle(self._runtime.control, event)
        if event.button == controls.BUTTON_LEFT:
            self.click()
        return controls.PointerClick()

    def fade_out(self):
        self._runtime.fade_out_with_particle(self._event_center)

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
        for event_type, handler in (
            (EventType.FRAME, self._on_frame),
            (EventType.MOUSE_MOVE, self._on_mouse_move),
            (EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response),
            (EventType.UI_CREATE, self._on_ui_create),
            (EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle),
        ):
            try:
                self._event_center.unsubscribe(event_type, handler)
            except Exception:
                pass
        self._runtime.cleanup()

    def fade_in(self):
        self._runtime.fade_in()
