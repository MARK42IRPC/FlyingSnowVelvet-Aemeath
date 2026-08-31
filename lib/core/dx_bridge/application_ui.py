"""Qt-free application UI hosted by native DirectX windows."""
from __future__ import annotations

from collections.abc import Callable
import time

from config.config import ANIMATION, BUBBLE_CONFIG, COMMAND_DIALOG, UI
from config.scale import scale_px
from lib.core.application_ui import ApplicationUiHost
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.graphics.application_visuals import (
    BubbleVisualDescription,
    CommandActionPanelLayout,
    application_tooltip_text,
    build_bubble_visual,
    build_command_action_panel_visual,
    build_mic_stt_indicator_visual,
    build_notice_panel_visual,
    build_qr_panel_visual,
    build_tooltip_visual,
    create_portable_command_hint_metrics,
    create_portable_bubble_text_metrics,
    decode_panel_image,
    notice_panel_size,
    qr_panel_action_text,
    qr_panel_size,
    resolve_bubble_geometry,
    resolve_command_action_panel_layout,
    resolve_qr_panel_layout,
)
from lib.core.graphics.commands import DrawBatch, scale_batch_alpha
from lib.core.graphics.resources import ImageResource
from lib.core.graphics.screen import clamp_rect_position
from lib.core.graphics.types import Point, Rect, Size
from lib.core.graphics.visuals import (
    build_command_panel_batch,
    resolve_command_panel_geometry,
)
from lib.core.input.types import Key, MouseButton
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager
from lib.core.logger import get_logger
from lib.core.desktop_actions import dispatch_desktop_action
from lib.core.world_objects import WorldObjectInstance

from .loop import DxLoopContext, DxScheduledCall
from .opacity import DxOpacityAnimator
from .clipboard import write_clipboard_text
from .announcement import DxAnnouncementWindow
from .command_hint import DxCommandHintWindow
from .screen import DxScreenProvider, get_cursor_position
from .speaker_search import DxSpeakerSearchWindow
from .text_metrics import create_directwrite_text_metrics
from .window_host import DxWindowHost


_logger = get_logger(__name__)


class _DxTooltipWindow:
    def __init__(self, context, screen_provider, *, window_host_factory, warp):
        self._context = context
        self._screen_provider = screen_provider
        self._window_host_factory = window_host_factory
        self._warp = bool(warp)
        self._metrics = create_portable_bubble_text_metrics()
        self._host = None
        self._visual = None
        self._text = ""
        self._anchor = Point()
        self._show_call = None
        self._hide_call = None
        self._request_generation = 0
        self._cleanup_done = False
        self._opacity = DxOpacityAnimator(context, self._request_repaint)

    def _request_repaint(self) -> None:
        if self._host is not None:
            self._host.request_repaint()

    @property
    def host(self):
        return self._host

    def _build(self):
        return build_tooltip_visual(
            self._text,
            self._metrics,
            opacity=float(UI.get("tooltip_opacity", 0.8)),
        )

    def _geometry(self) -> Rect:
        size = self._visual.size
        screen = self._screen_provider.get_screen_rect_for_point(self._anchor)
        scale = self._screen_provider.get_scale_for_point(self._anchor)
        physical_width = size.width * scale
        physical_height = size.height * scale
        gap = scale_px(10, min_abs=1) * scale
        x = self._anchor.x + gap
        y = self._anchor.y
        if x + physical_width > screen.x + screen.width:
            x = self._anchor.x - physical_width - gap
        x, y, _ = clamp_rect_position(
            int(round(x)),
            int(round(y)),
            int(round(physical_width)),
            int(round(physical_height)),
            screen,
        )
        return Rect(x, y, size.width, size.height)

    def _ensure_host(self):
        if self._host is not None:
            return self._host
        geometry = self._geometry()
        host = self._window_host_factory(
            int(geometry.width),
            int(geometry.height),
            x=int(geometry.x),
            y=int(geometry.y),
            callbacks=self,
            warp=self._warp,
            topmost=True,
            tool_window=True,
            no_activate=True,
            clickthrough=True,
            logical_content=True,
        )
        self._context.register_poller(host)
        get_layer_manager().register(host, Layer.TOOLTIP, name="DxTooltip")
        self._host = host
        native_metrics = create_directwrite_text_metrics(host)
        if native_metrics is not None:
            self._metrics = native_metrics
            self._visual = self._build()
        return host

    def request(self, text: str, point: Point) -> None:
        value = str(text or "").strip()
        if not value or not isinstance(point, Point):
            self.hide()
            return
        if value == self._text and point == self._anchor and (
            self._show_call is not None
            or (self._host is not None and self._host.is_visible())
        ):
            return
        self.hide()
        self._text = value
        self._anchor = point
        self._request_generation += 1
        generation = self._request_generation
        self._show_call = self._context.call_later(
            1000,
            lambda: self._show_if_current(generation),
        )

    def _show_if_current(self, generation: int) -> None:
        self._show_call = None
        if generation != self._request_generation or not self._text:
            return
        self._visual = self._build()
        host = self._ensure_host()
        host.set_geometry(self._geometry())
        host.show()
        self._opacity.fade_in()
        host.request_repaint()
        self._hide_call = self._context.call_later(5000, self.hide)

    def hide(self) -> None:
        self._request_generation += 1
        for name in ("_show_call", "_hide_call"):
            call = getattr(self, name)
            setattr(self, name, None)
            if call is not None:
                call.cancel()
        host = self._host
        if host is not None and host.is_visible():
            self._opacity.fade_out(host.hide)

    def prepare_render(self) -> DrawBatch:
        batch = self._visual.batch if self._visual is not None else DrawBatch()
        return scale_batch_alpha(batch, self._opacity.value)

    def handle_pointer_press(self, event):
        return None
    def handle_pointer_release(self, button):
        return None
    def handle_pointer_enter(self):
        return None
    def handle_pointer_leave(self):
        return None
    def handle_pointer_move(self, event):
        return None
    def handle_key_press(self, event):
        return None
    def handle_key_release(self, event):
        return None
    def handle_window_moved(self, position):
        return None
    def handle_host_close(self):
        self.hide()

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._opacity.cancel()
        self.hide()
        host, self._host = self._host, None
        if host is not None:
            get_layer_manager().unregister(host)
            self._context.unregister_poller(host)
            host.cleanup()


class _DxBubbleWindow:
    """Native host for the Qt-baseline chat bubble presenter."""

    def __init__(self, context, screen_provider, *, window_host_factory, warp, entity_provider, clipboard_writer=write_clipboard_text, tooltip_requester=None, tooltip_hider=None):
        self._context = context
        self._screen_provider = screen_provider
        self._window_host_factory = window_host_factory
        self._warp = bool(warp)
        self._entity_provider = entity_provider
        self._clipboard_writer = clipboard_writer
        self._tooltip_requester = tooltip_requester
        self._tooltip_hider = tooltip_hider
        self._event_center = get_event_center()
        self._clickthrough = False
        self._metrics = create_portable_bubble_text_metrics()
        self._host = None
        self._description: BubbleVisualDescription | None = None
        self._current: tuple[str, int, int, str, str, str, str] | None = None
        self._queue: list[tuple[str, int, int, str, str, str, str]] = []
        self._elapsed = 0
        self._hide_call = None
        self._cleanup_done = False
        self._opacity = DxOpacityAnimator(context, self._request_repaint)

    def _request_repaint(self) -> None:
        if self._host is not None:
            self._host.request_repaint()

    @property
    def host(self):
        return self._host

    def _pet_anchor(self) -> Point:
        entity = self._entity_provider()
        if isinstance(entity, Rect):
            geometry = entity
        else:
            getter = getattr(entity, "get_core_geometry", None)
            geometry = getter() if callable(getter) else None
        if isinstance(geometry, Rect):
            return Point(geometry.x + geometry.width / 2.0, geometry.y)
        screen = self._screen_provider.get_primary_screen_rect()
        return Point(screen.x + screen.width / 2.0, screen.y + screen.height / 2.0)

    def _build(self, text: str, align: str) -> BubbleVisualDescription:
        return build_bubble_visual(
            text,
            self._metrics,
            max_width=float(BUBBLE_CONFIG.get("max_width", UI.get("bubble_max_width", 360))),
            padding=float(BUBBLE_CONFIG.get("padding", 12)),
            border_width=float(BUBBLE_CONFIG.get("border_width", 2)),
            align=align,
            layer=int(Layer.PET_UI),
        )

    def _ensure_host(self, description: BubbleVisualDescription):
        if self._host is not None:
            return self._host
        anchor = self._pet_anchor()
        geometry = self._bubble_geometry(description)
        host = self._window_host_factory(
            int(geometry.width), int(geometry.height),
            x=int(geometry.x), y=int(geometry.y), callbacks=self,
            warp=self._warp, topmost=True, tool_window=True,
            no_activate=True, clickthrough=self._clickthrough,
            logical_content=True,
        )
        self._context.register_poller(host)
        get_layer_manager().register(host, Layer.PET_UI, name="DxBubble")
        self._host = host
        native_metrics = create_directwrite_text_metrics(host)
        if native_metrics is not None:
            self._metrics = native_metrics
            self._description = self._build(
                self._current[0] if self._current is not None else "",
                self._current[3] if self._current is not None else "center",
            )
        return host

    def _bubble_geometry(self, description: BubbleVisualDescription) -> Rect:
        anchor = self._pet_anchor()
        screen = self._screen_provider.get_screen_rect_for_point(anchor)
        scale = self._screen_provider.get_scale_for_point(anchor)
        physical = resolve_bubble_geometry(
            anchor,
            Size(
                description.size.width * scale,
                description.size.height * scale,
            ),
            screen,
        )
        return Rect(
            physical.x,
            physical.y,
            description.size.width,
            description.size.height,
        )

    def _show_current(self):
        if self._current is None:
            return
        text, _min_ticks, max_ticks, align, _source, _task_id, _kind = self._current
        self._description = self._build(text, align)
        host = self._ensure_host(self._description)
        host.set_geometry(self._bubble_geometry(self._description))
        host.show()
        self._opacity.fade_in()
        host.request_repaint()
        self._elapsed = 0
        self._cancel_hide()
        self._hide_call = self._context.call_later(max(1, int(max_ticks)) * 50, self.hide)

    def show(
        self,
        text: str,
        min_ticks: int = 40,
        max_ticks: int = 100,
        align: str = "center",
        *,
        force_replace: bool = False,
        source: str = "",
        task_id: str = "",
        kind: str = "",
    ):
        item = (
            str(text or ""),
            max(0, int(min_ticks)),
            max(1, int(max_ticks)),
            str(align or "center"),
            str(source or ""),
            str(task_id or ""),
            str(kind or ""),
        )
        if not item[0]:
            return
        replacing_visible = (
            self._current is not None
            and self._host is not None
            and self._host.is_visible()
        )
        if force_replace:
            if replacing_visible:
                self._publish_rect_particle("up_fade")
            self._queue.clear()
            self._current = item
            self._show_current()
        elif self._current is None:
            self._current = item
            self._show_current()
        elif self._elapsed >= self._current[1]:
            if replacing_visible:
                self._publish_rect_particle("up_fade")
            self._current = item
            self._show_current()
        else:
            self._queue.append(item)

    @staticmethod
    def _metadata_matches(
        item,
        *,
        source: str,
        task_id: str,
        kind: str,
    ) -> bool:
        return (
            (not source or item[4] == str(source))
            and (not task_id or item[5] == str(task_id))
            and (not kind or item[6] == str(kind))
        )

    def remove_bubbles(self, *, source: str = "", task_id: str = "", kind: str = "") -> None:
        self._queue = [
            item for item in self._queue
            if not self._metadata_matches(
                item,
                source=source,
                task_id=task_id,
                kind=kind,
            )
        ]
        if self._current is not None and self._metadata_matches(
            self._current,
            source=source,
            task_id=task_id,
            kind=kind,
        ):
            self.hide()

    def hide(self):
        self._cancel_hide()
        was_visible = self._host is not None and self._host.is_visible()
        if was_visible:
            self._publish_rect_particle("right_fade")
            self._opacity.fade_out(self._host.hide)
        self._current = None
        if self._queue:
            item = self._queue.pop(0)
            self._current = item
            self._show_current()

    def clear(self):
        self._queue.clear()
        self.hide()

    def tick(self):
        if self._current is not None:
            self._elapsed += 1

    def prepare_render(self) -> DrawBatch:
        batch = self._description.batch if self._description is not None else DrawBatch()
        return scale_batch_alpha(batch, self._opacity.value)

    def set_anchor_entity(self, entity):
        if self._host is None or not self._host.is_visible() or self._description is None:
            return
        self._host.set_geometry(self._bubble_geometry(self._description))

    def handle_pointer_press(self, event):
        button = getattr(event, "button", MouseButton.NONE)
        if button in {MouseButton.LEFT, MouseButton.RIGHT}:
            point = getattr(event, "screen_pos", None)
            if not isinstance(point, Point):
                local = getattr(event, "pos", Point())
                getter = getattr(self._host, "get_geometry", None)
                geometry = getter() if callable(getter) else getattr(self._host, "geometry", Rect())
                point = Point(geometry.x + local.x, geometry.y + local.y)
            self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
                "particle_id": "click" if button == MouseButton.LEFT else "pink_click",
                "area_type": "point",
                "area_data": (point.x, point.y),
            }))
        if button == MouseButton.LEFT:
            self.hide()
        elif button == MouseButton.RIGHT:
            if self._current is not None:
                self._clipboard_writer(self._current[0])
            self.hide()

    def handle_pointer_release(self, button):
        return None
    def handle_pointer_enter(self):
        return None
    def handle_pointer_leave(self):
        if callable(self._tooltip_hider):
            self._tooltip_hider()
    def handle_pointer_move(self, event):
        requester = self._tooltip_requester
        if callable(requester):
            requester(
                application_tooltip_text("bubble"),
                getattr(event, "screen_pos", Point()),
            )
    def handle_key_press(self, event):
        if getattr(event, "key", Key.UNKNOWN) == Key.ESCAPE:
            self.hide()
    def handle_key_release(self, event):
        return None
    def handle_window_moved(self, position):
        self.set_anchor_entity(None)
    def handle_host_close(self):
        self.hide()

    def _cancel_hide(self):
        call, self._hide_call = self._hide_call, None
        if call is not None:
            call.cancel()

    def _publish_rect_particle(self, particle_id: str) -> None:
        if self._host is None:
            return
        getter = getattr(self._host, "get_geometry", None)
        rect = getter() if callable(getter) else getattr(self._host, "geometry", Rect())
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": particle_id,
            "area_type": "rect",
            "area_data": (
                rect.x,
                rect.y,
                rect.x + rect.width,
                rect.y + rect.height,
            ),
        }))

    def set_clickthrough(self, enabled: bool) -> None:
        self._clickthrough = bool(enabled)
        if self._host is not None:
            setter = getattr(self._host, "set_clickthrough", None)
            if callable(setter):
                setter(self._clickthrough)

    def cleanup(self):
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._cancel_hide()
        self._opacity.cancel()
        host, self._host = self._host, None
        if host is not None:
            get_layer_manager().unregister(host)
            self._context.unregister_poller(host)
            host.cleanup()


class _DxCommandActionPanel:
    """One native window executing the shared eight-button action batch."""

    def __init__(self, context, screen_provider, *, window_host_factory, warp, on_action, tooltip_requester=None, tooltip_hider=None):
        self._context = context
        self._screen_provider = screen_provider
        self._window_host_factory = window_host_factory
        self._warp = bool(warp)
        self._on_action = on_action
        self._tooltip_requester = tooltip_requester
        self._tooltip_hider = tooltip_hider
        self._event_center = get_event_center()
        self._host = None
        self._command_rect: Rect | None = None
        self._hovered = ""
        self._pressed = ""
        self._interaction_mode = "companion"
        self._batch = DrawBatch()
        self._cleanup_done = False
        self._clickthrough = False
        self._opacity = DxOpacityAnimator(context, self._request_repaint)

    def _request_repaint(self) -> None:
        if self._host is not None:
            self._host.request_repaint()

    @property
    def host(self):
        return self._host

    def _layout(self):
        anchor = self._command_rect or Rect()
        layout = resolve_command_action_panel_layout(anchor)
        scale = self._screen_provider.get_scale_for_point(Point(
            anchor.x + anchor.width / 2.0,
            anchor.y + anchor.height / 2.0,
        ))
        if scale == 1.0:
            return layout
        rects = tuple((name, Rect(
            anchor.x + (rect.x - anchor.x) * scale,
            anchor.y + (rect.y - anchor.y) * scale,
            rect.width * scale,
            rect.height * scale,
        )) for name, rect in layout.rects)
        return CommandActionPanelLayout(
            Size(layout.size.width * scale, layout.size.height * scale),
            rects,
        )

    def _logical_layout(self):
        return resolve_command_action_panel_layout(self._command_rect or Rect())

    def _rebuild(self):
        if self._command_rect is None:
            self._batch = DrawBatch()
            return
        visual = build_command_action_panel_visual(
            self._command_rect,
            hovered=self._hovered,
            pressed=self._pressed,
            interaction_mode=self._interaction_mode,
        )
        self._batch = visual.batch

    def _ensure_host(self):
        if self._host is not None:
            return self._host
        layout = self._layout()
        logical_layout = self._logical_layout()
        origin_x = min(rect.x for _name, rect in layout.rects)
        origin_y = min(rect.y for _name, rect in layout.rects)
        host = self._window_host_factory(
            int(logical_layout.size.width), int(logical_layout.size.height),
            x=int(origin_x), y=int(origin_y), callbacks=self,
            warp=self._warp, topmost=True, tool_window=True,
            no_activate=False, clickthrough=self._clickthrough,
            logical_content=True,
        )
        self._context.register_poller(host)
        get_layer_manager().register(host, Layer.PET_UI, name="DxCommandActionPanel")
        self._host = host
        return host

    def set_command_rect(self, rect: Rect | None):
        self._command_rect = rect if isinstance(rect, Rect) else None
        if self._command_rect is None:
            self.hide()
            return
        self._rebuild()
        if self._host is not None:
            layout = self._layout()
            logical_layout = self._logical_layout()
            origin_x = min(item.x for _name, item in layout.rects)
            origin_y = min(item.y for _name, item in layout.rects)
            self._host.set_geometry(Rect(
                origin_x, origin_y,
                logical_layout.size.width, logical_layout.size.height,
            ))
            self._host.request_repaint()

    def set_interaction_mode(self, mode: str) -> None:
        normalized = "office" if str(mode).lower() == "office" else "companion"
        if normalized == self._interaction_mode:
            return
        self._interaction_mode = normalized
        self._rebuild()
        if self._host is not None:
            self._host.request_repaint()

    def show(self):
        if self._command_rect is None:
            return
        self._rebuild()
        host = self._ensure_host()
        layout = self._layout()
        logical_layout = self._logical_layout()
        origin_x = min(item.x for _name, item in layout.rects)
        origin_y = min(item.y for _name, item in layout.rects)
        host.set_geometry(Rect(
            origin_x, origin_y,
            logical_layout.size.width, logical_layout.size.height,
        ))
        host.show()
        self._opacity.fade_in()
        host.request_repaint()

    def hide(self):
        self._hovered = ""
        self._pressed = ""
        host = self._host
        if host is not None and host.is_visible():
            self._opacity.fade_out(host.hide)

    def set_clickthrough(self, enabled: bool) -> None:
        self._clickthrough = bool(enabled)
        if self._host is not None:
            setter = getattr(self._host, "set_clickthrough", None)
            if callable(setter):
                setter(self._clickthrough)

    def prepare_render(self):
        return scale_batch_alpha(self._batch, self._opacity.value)

    def _hit(self, pos: Point) -> str:
        layout = self._layout()
        for name, rect in layout.rects:
            if rect.x <= pos.x < rect.x + rect.width and rect.y <= pos.y < rect.y + rect.height:
                return name
        return ""

    def handle_pointer_move(self, event):
        pos = getattr(event, "global_pos", getattr(event, "screen_pos", Point()))
        name = self._hit(pos)
        if name != self._hovered:
            self._hovered = name
            self._rebuild()
            if self._host is not None:
                self._host.request_repaint()
        if name and callable(self._tooltip_requester):
            self._tooltip_requester(application_tooltip_text(name), pos)
        elif callable(self._tooltip_hider):
            self._tooltip_hider()

    def handle_pointer_press(self, event):
        if getattr(event, "button", MouseButton.NONE) != MouseButton.LEFT:
            return
        pos = getattr(event, "global_pos", getattr(event, "screen_pos", Point()))
        self._pressed = self._hit(pos)
        if self._pressed:
            self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
                "particle_id": "click",
                "area_type": "point",
                "area_data": (pos.x, pos.y),
            }))
        self._rebuild()
        if self._host is not None:
            capture = getattr(self._host, "capture_mouse", None)
            if callable(capture):
                capture()
            self._host.request_repaint()

    def handle_pointer_release(self, button):
        if button != MouseButton.LEFT:
            return
        name = self._pressed if self._pressed == self._hovered else ""
        self._pressed = ""
        self._rebuild()
        if self._host is not None:
            release = getattr(self._host, "release_mouse", None)
            if callable(release):
                release()
            self._host.request_repaint()
        if name:
            self._on_action(name)

    def handle_pointer_enter(self):
        return None
    def handle_pointer_leave(self):
        if callable(self._tooltip_hider):
            self._tooltip_hider()
        if not self._pressed:
            self._hovered = ""
        self._rebuild()
        if self._host is not None:
            self._host.request_repaint()
    def handle_key_press(self, event):
        return None
    def handle_key_release(self, event):
        return None
    def handle_window_moved(self, position):
        return None
    def handle_host_close(self):
        self.hide()

    def cleanup(self):
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._opacity.cancel()
        host, self._host = self._host, None
        if host is not None:
            get_layer_manager().unregister(host)
            self._context.unregister_poller(host)
            host.cleanup()


class _DxPanelWindow:
    """Small lazy native panel shared by command, notice and QR surfaces."""

    def __init__(
        self,
        context: DxLoopContext,
        screen_provider: DxScreenProvider,
        *,
        name: str,
        layer: Layer,
        size: tuple[int, int],
        interactive: bool,
        window_host_factory: Callable[..., DxWindowHost],
        warp: bool,
        on_submit: Callable[[str], None] | None = None,
        action_text: str = "",
        on_action: Callable[[], None] | None = None,
        on_input_changed: Callable[[str], None] | None = None,
        on_navigation: Callable[[Key | int], str] | None = None,
        on_visibility_changed: Callable[[bool], None] | None = None,
        on_geometry_changed: Callable[[Rect], None] | None = None,
        tooltip_text: str = "",
        tooltip_requester: Callable[[str, Point], None] | None = None,
        tooltip_hider: Callable[[], None] | None = None,
    ) -> None:
        self._context = context
        self._screen_provider = screen_provider
        self._name = name
        self._layer = layer
        self._size = size
        self._interactive = interactive
        self._window_host_factory = window_host_factory
        self._warp = bool(warp)
        self._on_submit = on_submit
        self._action_text = str(action_text or "").strip()
        self._on_action = on_action
        self._on_input_changed = on_input_changed
        self._on_navigation = on_navigation
        self._on_visibility_changed = on_visibility_changed
        self._on_geometry_changed = on_geometry_changed
        self._tooltip_text = str(tooltip_text or "")
        self._tooltip_requester = tooltip_requester
        self._tooltip_hider = tooltip_hider
        self._host: DxWindowHost | None = None
        self._batch = DrawBatch()
        self._title = ""
        self._text = ""
        self._input = ""
        self._composition = ""
        self._qr_resource: ImageResource | None = None
        self._surface_kind = "notice"
        self._hide_call: DxScheduledCall | None = None
        self._cleanup_done = False
        self._clickthrough = not self._interactive
        self._anchor_rect: Rect | None = None
        self._action_hovered = False
        self._action_pressed = False
        self._visible = False
        self._opacity = DxOpacityAnimator(context, self._request_repaint)

    def _request_repaint(self) -> None:
        if self._host is not None:
            self._host.request_repaint()

    @property
    def host(self) -> DxWindowHost | None:
        return self._host

    def _geometry(self) -> Rect:
        screen = self._screen_provider.get_primary_screen_rect()
        width, height = self._size
        if self._interactive and self._anchor_rect is not None:
            anchor = self._anchor_rect
            point = Point(
                anchor.x + anchor.width / 2.0,
                anchor.y + anchor.height / 2.0,
            )
            screen = self._screen_provider.get_screen_rect_for_point(point)
            scale = self._screen_provider.get_scale_for_point(point)
            physical = resolve_command_panel_geometry(
                anchor,
                (width * scale, height * scale),
                screen,
                offset_x=float(COMMAND_DIALOG.get("offset_x", 6)) * scale,
                offset_y=float(COMMAND_DIALOG.get("offset_y", 0)) * scale,
            )
            return Rect(physical.x, physical.y, width, height)
        point = Point(
            screen.x + screen.width / 2.0,
            screen.y + screen.height / 2.0,
        )
        scale = self._screen_provider.get_scale_for_point(point)
        physical_width = width * scale
        physical_height = height * scale
        if self._interactive:
            x = screen.x + (screen.width - physical_width) / 2.0
            y = screen.y + (screen.height - physical_height) / 2.0
        else:
            x = screen.x + screen.width - physical_width - 24 * scale
            y = screen.y + screen.height - physical_height - 48 * scale
        return Rect(x, y, width, height)

    def set_anchor_rect(self, geometry: Rect | None) -> None:
        self._anchor_rect = geometry if isinstance(geometry, Rect) else None
        if self.is_visible() and self._host is not None:
            resolved = self._geometry()
            self._host.set_geometry(resolved)
            if callable(self._on_geometry_changed):
                self._on_geometry_changed(self._host.get_geometry())

    def _ensure_host(self) -> DxWindowHost:
        if self._cleanup_done:
            raise RuntimeError("DX application panel has been cleaned")
        if self._host is not None:
            return self._host
        geometry = self._geometry()
        host = self._window_host_factory(
            int(geometry.width),
            int(geometry.height),
            x=int(round(geometry.x)),
            y=int(round(geometry.y)),
            callbacks=self,
            warp=self._warp,
            topmost=True,
            tool_window=True,
            no_activate=not self._interactive,
            clickthrough=self._clickthrough,
            logical_content=True,
        )
        try:
            self._context.register_poller(host)
            get_layer_manager().register(host, self._layer, name=self._name)
        except Exception:
            self._context.unregister_poller(host)
            host.cleanup()
            raise
        self._host = host
        return host

    def is_visible(self) -> bool:
        return self._visible

    def show_notice(self, text: str, *, title: str = "飞行雪绒", timeout_ms: int = 5000) -> None:
        self._title = str(title or "飞行雪绒")
        self._text = str(text or "")
        self._input = ""
        self._composition = ""
        self._qr_resource = None
        self._action_hovered = False
        self._action_pressed = False
        self._surface_kind = "notice"
        self._rebuild_batch()
        self._show()
        self._schedule_hide(timeout_ms)

    def show_command(self) -> None:
        self._title = "输入消息或命令"
        self._text = ""
        self._input = ""
        self._composition = ""
        self._qr_resource = None
        self._action_hovered = False
        self._action_pressed = False
        self._surface_kind = "command"
        self._notify_input_changed()
        self._rebuild_batch()
        self._show()

    def show_qr(self, payload: dict, *, default_title: str) -> None:
        self._title = str(payload.get("title") or default_title)
        self._text = str(payload.get("status") or "二维码准备中...")
        self._input = ""
        self._composition = ""
        self._action_hovered = False
        self._action_pressed = False
        self._surface_kind = "qr"
        qr_png = payload.get("qr_png")
        if isinstance(qr_png, (bytes, bytearray)) and qr_png:
            self._set_qr_png(bytes(qr_png))
        elif payload.get("qr_png") is not None:
            self._qr_resource = None
        self._rebuild_batch()
        self._show()
        if payload.get("logged_in"):
            self._schedule_hide(1500)

    def _set_qr_png(self, payload: bytes) -> None:
        self._qr_resource = decode_panel_image(
            payload,
            resource_prefix="application-qr",
        )

    def _show(self) -> None:
        self._cancel_hide()
        host = self._ensure_host()
        geometry = self._geometry()
        host.set_geometry(geometry)
        if not host.is_visible():
            host.show()
            if self._interactive:
                set_ime_position = getattr(host, "set_ime_position", None)
                if callable(set_ime_position):
                    set_ime_position(
                        10 if self._on_submit is not None else 30,
                        max(8, int(self._size[1]) - 8),
                    )
            host.activate()
        self._visible = True
        self._opacity.fade_in()
        get_layer_manager().enforce_burst()
        host.request_repaint()
        if callable(self._on_geometry_changed):
            self._on_geometry_changed(host.get_geometry())
        if callable(self._on_visibility_changed):
            self._on_visibility_changed(True)

    def hide(self) -> None:
        self._cancel_hide()
        was_visible = self.is_visible()
        self._visible = False
        self._composition = ""
        host = self._host
        if host is not None:
            self._opacity.fade_out(host.hide)
        if was_visible and callable(self._on_visibility_changed):
            self._on_visibility_changed(False)

    def set_clickthrough(self, enabled: bool) -> None:
        self._clickthrough = bool(enabled) or not self._interactive
        if self._host is not None:
            setter = getattr(self._host, "set_clickthrough", None)
            if callable(setter):
                setter(self._clickthrough)

    def _notify_input_changed(self) -> None:
        if callable(self._on_input_changed):
            self._on_input_changed(self._input)

    def set_input_text(self, text: str) -> None:
        self._input = str(text or "")[:512]
        self._composition = ""
        self._notify_input_changed()
        self._rebuild_batch()
        if self._host is not None:
            self._host.request_repaint()

    def toggle_command(self) -> None:
        if self.is_visible():
            self.hide()
        else:
            self.show_command()

    def _schedule_hide(self, timeout_ms: int) -> None:
        self._cancel_hide()
        if int(timeout_ms) > 0:
            self._hide_call = self._context.call_later(timeout_ms, self.hide)

    def _cancel_hide(self) -> None:
        call, self._hide_call = self._hide_call, None
        if call is not None:
            call.cancel()

    def _rebuild_batch(self) -> None:
        width, height = self._size
        if self._on_submit is not None:
            self._batch = build_command_panel_batch(
                width,
                height,
                self._input,
                self._composition,
                layer=int(self._layer),
            )
            return
        if self._surface_kind == "qr":
            self._batch = build_qr_panel_visual(
                self._title,
                self._text,
                "二维码准备中...",
                self._qr_resource,
                size=(width, height),
                layer=int(self._layer),
                action_text=self._action_text,
                action_state=("pressed" if self._action_pressed and self._action_hovered else
                              "hover" if self._action_hovered else "normal"),
            ).batch
            return
        self._batch = build_notice_panel_visual(
            self._text,
            title=self._title,
            size=(width, height),
            layer=int(self._layer),
        ).batch

    def prepare_render(self) -> DrawBatch:
        return scale_batch_alpha(self._batch, self._opacity.value)

    def handle_key_press(self, event: object) -> None:
        key = getattr(event, "key", Key.UNKNOWN)
        if key == Key.ESCAPE:
            self.hide()
            return
        if self._on_submit is None:
            return
        if key in (Key.TAB, Key.UP, Key.DOWN, Key.LEFT, Key.RIGHT):
            replacement = self._on_navigation(key) if callable(self._on_navigation) else ""
            if replacement:
                self.set_input_text(replacement)
            return
        if key in (Key.RETURN, Key.ENTER):
            if self._composition:
                return
            value = self._input.strip()
            if value:
                self._input = ""
                self._composition = ""
                self._notify_input_changed()
                self._rebuild_batch()
                self.hide()
                self._on_submit(value)
            return
        if key == Key.BACKSPACE:
            if self._composition:
                return
            self._input = self._input[:-1]
            self._notify_input_changed()
        self._rebuild_batch()
        if self._host is not None:
            self._host.request_repaint()

    def handle_text_input(self, text: str) -> None:
        if self._on_submit is None:
            return
        value = "".join(character for character in str(text or "") if character.isprintable())
        if not value:
            return
        remaining = max(0, 512 - len(self._input))
        if remaining:
            self._input += value[:remaining]
        self._composition = ""
        self._notify_input_changed()
        self._rebuild_batch()
        if self._host is not None:
            self._host.request_repaint()

    def handle_ime_composition(self, text: str) -> None:
        if self._on_submit is None:
            return
        remaining = max(0, 512 - len(self._input))
        self._composition = str(text or "")[:remaining]
        self._rebuild_batch()
        if self._host is not None:
            self._host.request_repaint()

    def handle_ime_end(self) -> None:
        if not self._composition:
            return
        self._composition = ""
        self._rebuild_batch()
        if self._host is not None:
            self._host.request_repaint()

    def handle_key_release(self, event: object) -> None:
        return None

    def handle_pointer_press(self, event: object) -> None:
        if self._surface_kind == "qr":
            pos = getattr(event, "pos", Point())
            layout = resolve_qr_panel_layout(self._size)
            inside = (
                layout.action_rect.x <= pos.x < layout.action_rect.x + layout.action_rect.width
                and layout.action_rect.y <= pos.y < layout.action_rect.y + layout.action_rect.height
            )
            if getattr(event, "button", MouseButton.NONE) == MouseButton.LEFT and inside:
                self._action_pressed = True
                self._action_hovered = True
                self._rebuild_batch()
                if self._host is not None:
                    capture = getattr(self._host, "capture_mouse", None)
                    if callable(capture):
                        capture()
                    self._host.request_repaint()
                return
        if self._interactive and self._host is not None:
            self._host.activate()

    def handle_pointer_release(self, button: MouseButton) -> None:
        if self._surface_kind == "qr":
            was_pressed = (
                self._action_pressed
                and self._action_hovered
                and button == MouseButton.LEFT
            )
            self._action_pressed = False
            self._rebuild_batch()
            if self._host is not None:
                release = getattr(self._host, "release_mouse", None)
                if callable(release):
                    release()
                self._host.request_repaint()
            if was_pressed:
                callback = self._on_action
                if callable(callback):
                    callback()
                self.hide()
            return
        return None

    def handle_pointer_enter(self) -> None:
        return None

    def handle_pointer_leave(self) -> None:
        if callable(self._tooltip_hider):
            self._tooltip_hider()
        if self._surface_kind == "qr":
            self._action_hovered = False
            if not self._action_pressed:
                self._rebuild_batch()
                if self._host is not None:
                    self._host.request_repaint()

    def handle_pointer_move(self, event: object) -> None:
        if self._tooltip_text and callable(self._tooltip_requester):
            self._tooltip_requester(
                self._tooltip_text,
                getattr(event, "screen_pos", Point()),
            )
        if self._surface_kind != "qr":
            return None
        pos = getattr(event, "pos", Point())
        layout = resolve_qr_panel_layout(self._size)
        hovered = (
            layout.action_rect.x <= pos.x < layout.action_rect.x + layout.action_rect.width
            and layout.action_rect.y <= pos.y < layout.action_rect.y + layout.action_rect.height
        )
        if hovered != self._action_hovered:
            self._action_hovered = hovered
            self._rebuild_batch()
            if self._host is not None:
                self._host.request_repaint()

    def handle_window_moved(self, position: Point) -> None:
        return None

    def handle_host_close(self) -> None:
        self.hide()

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._visible = False
        self._cancel_hide()
        self._opacity.cancel()
        host, self._host = self._host, None
        if host is None:
            return
        try:
            get_layer_manager().unregister(host)
        finally:
            self._context.unregister_poller(host)
        host.cleanup()


class _DxMicSttIndicator:
    """Native counterpart of the Qt microphone listening indicator."""

    def __init__(
        self,
        context,
        screen_provider,
        *,
        window_host_factory,
        warp,
        entity_provider,
        cursor_provider=get_cursor_position,
        tooltip_requester=None,
        tooltip_hider=None,
    ):
        self._context = context
        self._screen_provider = screen_provider
        self._window_host_factory = window_host_factory
        self._warp = bool(warp)
        self._entity_provider = entity_provider
        self._cursor_provider = cursor_provider
        self._tooltip_requester = tooltip_requester
        self._tooltip_hider = tooltip_hider
        self._event_center = get_event_center()
        self._visual = build_mic_stt_indicator_visual()
        self._host = None
        self._listening = False
        self._shown = False
        self._speech_active = False
        self._clickthrough = False
        self._last_cursor_near = time.monotonic()
        self._hover_radius = float(scale_px(120, min_abs=90))
        self._hide_delay = 2.0
        self._margin = float(scale_px(4, min_abs=3))
        self._extra_offset_y = float(scale_px(20, min_abs=20))
        self._cleanup_done = False
        self._opacity = DxOpacityAnimator(context, self._request_repaint)

    def _request_repaint(self) -> None:
        if self._host is not None:
            self._host.request_repaint()

    @property
    def host(self):
        return self._host

    def _pet_geometry(self) -> Rect | None:
        entity = self._entity_provider()
        if isinstance(entity, Rect):
            return entity
        getter = getattr(entity, "get_core_geometry", None)
        if callable(getter):
            geometry = getter()
            if isinstance(geometry, Rect):
                return geometry
        return None

    def _geometry(self) -> Rect:
        size = self._visual.size
        pet = self._pet_geometry()
        if pet is None:
            screen = self._screen_provider.get_primary_screen_rect()
            pet = Rect(
                screen.x + (screen.width - ANIMATION["pet_size"][0]) / 2.0,
                screen.y + (screen.height - ANIMATION["pet_size"][1]) / 2.0,
                ANIMATION["pet_size"][0],
                ANIMATION["pet_size"][1],
            )
        screen = self._screen_provider.get_screen_rect_for_point(pet.top_left)
        scale = self._screen_provider.get_scale_for_point(pet.top_left)
        x, y, _ = clamp_rect_position(
            int(round(pet.x + self._margin * scale)),
            int(round(
                pet.y - size.height * scale
                - self._margin * scale
                + self._extra_offset_y * scale
            )),
            int(round(size.width * scale)),
            int(round(size.height * scale)),
            screen,
        )
        return Rect(x, y, size.width, size.height)

    def _ensure_host(self):
        if self._cleanup_done:
            raise RuntimeError("DX microphone indicator has been cleaned")
        if self._host is not None:
            return self._host
        geometry = self._geometry()
        host = self._window_host_factory(
            int(geometry.width),
            int(geometry.height),
            x=int(geometry.x),
            y=int(geometry.y),
            callbacks=self,
            warp=self._warp,
            topmost=True,
            tool_window=True,
            no_activate=True,
            clickthrough=self._clickthrough,
            logical_content=True,
        )
        self._context.register_poller(host)
        get_layer_manager().register(host, Layer.PET_UI, name="DxMicSttIndicator")
        self._host = host
        return host

    def update_state(self, data: dict) -> None:
        listening = bool(data.get("is_listening"))
        speech_active = bool(data.get("speech_active"))
        if speech_active != self._speech_active:
            self._speech_active = speech_active
            self._visual = build_mic_stt_indicator_visual(
                speech_active=speech_active,
            )
        self._listening = listening
        if not listening:
            self.hide()
            return
        self._last_cursor_near = time.monotonic()
        host = self._ensure_host()
        host.set_geometry(self._geometry())
        host.show()
        self._shown = True
        self._opacity.fade_in()
        host.request_repaint()

    def tick(self) -> None:
        if not self._listening:
            return
        host = self._ensure_host()
        geometry = self._geometry()
        host.set_geometry(geometry)
        geometry = host.get_geometry()
        cursor = self._cursor_provider()
        center = Point(
            geometry.x + geometry.width / 2.0,
            geometry.y + geometry.height / 2.0,
        )
        distance_sq = (cursor.x - center.x) ** 2 + (cursor.y - center.y) ** 2
        now = time.monotonic()
        if distance_sq <= self._hover_radius ** 2:
            self._last_cursor_near = now
            if not self._shown:
                host.show()
                self._shown = True
                self._opacity.fade_in()
                host.request_repaint()
        elif self._shown and now - self._last_cursor_near >= self._hide_delay:
            self._shown = False
            self._opacity.fade_out(host.hide)

    def hide(self) -> None:
        self._shown = False
        host = self._host
        if host is not None and host.is_visible():
            self._opacity.fade_out(host.hide)

    def set_clickthrough(self, enabled: bool) -> None:
        self._clickthrough = bool(enabled)
        if self._host is not None:
            setter = getattr(self._host, "set_clickthrough", None)
            if callable(setter):
                setter(self._clickthrough)

    def prepare_render(self) -> DrawBatch:
        return scale_batch_alpha(self._visual.batch, self._opacity.value)

    def handle_pointer_press(self, event) -> None:
        if getattr(event, "button", MouseButton.NONE) != MouseButton.LEFT:
            return
        point = getattr(event, "screen_pos", Point())
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": "click",
            "area_type": "point",
            "area_data": (point.x, point.y),
        }))
        self._event_center.publish(Event(EventType.MIC_STT_STOP, {
            "source": "mic_stt_indicator",
        }))

    def handle_pointer_release(self, button):
        return None
    def handle_pointer_enter(self):
        return None
    def handle_pointer_leave(self):
        if callable(self._tooltip_hider):
            self._tooltip_hider()
    def handle_pointer_move(self, event):
        if callable(self._tooltip_requester):
            self._tooltip_requester(
                application_tooltip_text("mic_stt"),
                getattr(event, "screen_pos", Point()),
            )
    def handle_key_press(self, event):
        return None
    def handle_key_release(self, event):
        return None
    def handle_window_moved(self, position):
        return None
    def handle_host_close(self):
        self.hide()

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._shown = False
        self._opacity.cancel()
        host, self._host = self._host, None
        if host is not None:
            get_layer_manager().unregister(host)
            self._context.unregister_poller(host)
            host.cleanup()


class DxApplicationUiHost:
    """Own the essential application UI without importing a Qt module."""

    def __init__(
        self,
        context: DxLoopContext,
        *,
        screen_provider: DxScreenProvider | None = None,
        window_host_factory: Callable[..., DxWindowHost] | None = None,
        warp: bool = False,
        announcement_opener: Callable[[str], object] | None = None,
        workbench_opener: Callable[[str], bool] | None = None,
        launch_wuwa: Callable[[], object] | None = None,
        animation_factory: Callable[[], object] | None = None,
        animation_cleanup: Callable[[], None] | None = None,
        music_service_provider: Callable[[], object] | None = None,
        game_command_runtime_factory: Callable[[], object] | None = None,
        clipboard_writer: Callable[[str], bool] | None = None,
    ) -> None:
        self._context = context
        self._screen_provider = screen_provider or DxScreenProvider()
        self._window_host_factory = window_host_factory or DxWindowHost
        self._warp = bool(warp)
        del announcement_opener
        self._workbench_opener = workbench_opener
        self._launch_wuwa = launch_wuwa
        self._animation_cleanup = animation_cleanup
        self._music_service_provider = music_service_provider
        self._game_command_runtime_factory = game_command_runtime_factory
        self._game_command_runtime: object | None = None
        self._clipboard_writer = clipboard_writer or write_clipboard_text
        self._event_center = get_event_center()
        self._yuanbao_service: object | None = None
        self._panels: dict[str, _DxPanelWindow] = {}
        self._bubble: _DxBubbleWindow | None = None
        self._action_panel: _DxCommandActionPanel | None = None
        self._command_hint: DxCommandHintWindow | None = None
        self._speaker_search: DxSpeakerSearchWindow | None = None
        self._announcement: DxAnnouncementWindow | None = None
        self._mic_indicator: _DxMicSttIndicator | None = None
        self._tooltip: _DxTooltipWindow | None = None
        self._subscriptions: list[tuple[EventType, Callable[[Event], None]]] = []
        self._prepared = False
        self._started = False
        self._stopped = False
        self._cleaned = False
        self._finalized = False
        self._command_entity: object | None = None
        self._last_pet_geometry: Rect | None = None
        self._clickthrough_enabled = False
        self._chat_listening = False
        self._interaction_mode = "companion"
        self._animation = animation_factory() if callable(animation_factory) else None

    def prepare_application(self, application: object) -> None:
        self._context.assert_owner_thread()

    def _subscribe(self, event_type: EventType, callback: Callable[[Event], None]) -> None:
        self._event_center.subscribe(event_type, callback)
        self._subscriptions.append((event_type, callback))

    def prepare_runtime(self) -> None:
        if self._prepared or self._cleaned:
            return
        self._prepared = True
        self._stopped = False
        self._subscribe(EventType.INFORMATION, self._on_information)
        self._subscribe(EventType.TICK, self._on_tick)
        self._subscribe(EventType.UI_BUBBLE_HIDE, self._on_bubble_hide)
        self._subscribe(EventType.UI_BUBBLE_REMOVE, self._on_bubble_remove)
        self._subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_state)
        self._subscribe(EventType.INTERACTION_MODE_CHANGED, self._on_interaction_mode_changed)
        self._subscribe(EventType.OFFICE_APPROVAL_REQUEST, self._on_office_approval_request)
        self._subscribe(EventType.LOG_ERROR, self._on_log_error)
        self._subscribe(EventType.UI_COMMAND_TOGGLE, self._on_command_toggle)
        self._subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._subscribe(EventType.UI_OPEN_CMD_WINDOW, self._on_command_open)
        self._subscribe(
            EventType.UI_OPEN_CMD_WINDOW_WITH_COMMAND,
            self._on_command_execute,
        )
        self._subscribe(EventType.YUANBAO_LOGIN_QR_SHOW, self._on_yuanbao_qr)
        self._subscribe(EventType.YUANBAO_LOGIN_QR_STATUS, self._on_yuanbao_qr)
        self._subscribe(EventType.YUANBAO_LOGIN_QR_HIDE, self._on_yuanbao_hide)
        self._subscribe(EventType.MUSIC_LOGIN_QR_SHOW, self._on_music_qr)
        self._subscribe(EventType.MUSIC_LOGIN_QR_STATUS, self._on_music_qr)
        self._subscribe(EventType.MUSIC_LOGIN_QR_HIDE, self._on_music_hide)
        self._subscribe(EventType.MIC_STT_STATE_CHANGE, self._on_mic_stt_state_change)
        self._subscribe(
            EventType.SPEAKER_SEARCH_TOGGLE_REQUEST,
            self._on_speaker_search_toggle,
        )
        self._subscribe(EventType.MUSIC_STATUS_CHANGE, self._on_music_status_change)
        self._subscribe(
            EventType.MUSIC_LOGIN_STATUS_CHANGE,
            self._on_music_login_status_change,
        )
        if callable(self._game_command_runtime_factory):
            self._game_command_runtime = self._game_command_runtime_factory()

    def _bubble_window(self) -> _DxBubbleWindow:
        bubble = self._bubble
        if bubble is None:
            bubble = _DxBubbleWindow(
                self._context,
                self._screen_provider,
                window_host_factory=self._window_host_factory,
                warp=self._warp,
                entity_provider=lambda: self._command_entity or self._last_pet_geometry,
                clipboard_writer=self._clipboard_writer,
                tooltip_requester=self._request_tooltip,
                tooltip_hider=self._hide_tooltip,
            )
            bubble.set_clickthrough(self._clickthrough_enabled)
            self._bubble = bubble
        return bubble

    def _action_panel_window(self) -> _DxCommandActionPanel:
        panel = self._action_panel
        if panel is None:
            panel = _DxCommandActionPanel(
                self._context,
                self._screen_provider,
                window_host_factory=self._window_host_factory,
                warp=self._warp,
                on_action=self._on_command_action,
                tooltip_requester=self._request_tooltip,
                tooltip_hider=self._hide_tooltip,
            )
            panel.set_interaction_mode(self._interaction_mode)
            panel.set_clickthrough(self._clickthrough_enabled)
            self._action_panel = panel
        return panel

    def _tooltip_window(self) -> _DxTooltipWindow:
        tooltip = self._tooltip
        if tooltip is None:
            tooltip = _DxTooltipWindow(
                self._context,
                self._screen_provider,
                window_host_factory=self._window_host_factory,
                warp=self._warp,
            )
            self._tooltip = tooltip
        return tooltip

    def _request_tooltip(self, text: str, point: Point) -> None:
        if text:
            self._tooltip_window().request(text, point)

    def _hide_tooltip(self) -> None:
        if self._tooltip is not None:
            self._tooltip.hide()

    def _on_command_action(self, action: str) -> None:
        chat_listening = self._chat_listening
        dispatch_desktop_action(
            action,
            clickthrough_enabled=self._clickthrough_enabled,
            chat_listening=chat_listening,
            launch_wuwa=self._launch_wuwa,
        )
        if action == "chat_mode":
            self._chat_listening = not chat_listening

    def _on_clickthrough_state(self, event: Event) -> None:
        enabled = bool((event.data or {}).get("enabled", False))
        self._clickthrough_enabled = enabled
        if self._action_panel is not None:
            self._action_panel.set_clickthrough(enabled)
        if self._bubble is not None:
            self._bubble.set_clickthrough(enabled)
        if self._command_hint is not None:
            self._command_hint.set_clickthrough(enabled)
        if self._mic_indicator is not None:
            self._mic_indicator.set_clickthrough(enabled)
        for panel in self._panels.values():
            panel.set_clickthrough(enabled)
        if self._speaker_search is not None:
            self._speaker_search.set_clickthrough(enabled)

    def _on_interaction_mode_changed(self, event: Event) -> None:
        mode = str((event.data or {}).get("mode", ""))
        if mode not in {"companion", "office"}:
            return
        self._interaction_mode = mode
        if self._action_panel is not None:
            self._action_panel.set_interaction_mode(mode)

    def _on_office_approval_request(self, event: Event) -> None:
        del event
        if not self._open_workbench_helper("office"):
            self._notice_panel().show_notice(
                "办公权限窗口启动失败，请重新运行安装依赖后重试。",
                title="办公权限许可",
            )

    def _on_tick(self, event: Event) -> None:
        if self._bubble is not None:
            self._bubble.tick()
            self._bubble.set_anchor_entity(self._command_entity)
        if self._speaker_search is not None:
            self._speaker_search.tick()
        if self._mic_indicator is not None:
            self._mic_indicator.tick()
        self._auto_hide_command_family()

    def _auto_hide_command_family(self) -> None:
        command = self._panels.get("command")
        if command is None or not command.is_visible():
            return
        hosts = [command.host]
        if self._command_hint is not None:
            hosts.append(self._command_hint.host)
        if self._action_panel is not None:
            hosts.append(self._action_panel.host)
        cursor = get_cursor_position()
        nearest_sq = None
        for host in hosts:
            if host is None or not host.is_visible():
                continue
            getter = getattr(host, "get_geometry", None)
            geometry = getter() if callable(getter) else getattr(host, "geometry", None)
            if not isinstance(geometry, Rect):
                continue
            dx = cursor.x - (geometry.x + geometry.width / 2.0)
            dy = cursor.y - (geometry.y + geometry.height / 2.0)
            distance_sq = dx * dx + dy * dy
            nearest_sq = distance_sq if nearest_sq is None else min(nearest_sq, distance_sq)
        limit = float(UI.get("auto_hide_mouse_distance", scale_px(300, min_abs=1)))
        if nearest_sq is not None and nearest_sq > limit * limit:
            command.hide()

    def _mic_indicator_window(self) -> _DxMicSttIndicator:
        indicator = self._mic_indicator
        if indicator is None:
            indicator = _DxMicSttIndicator(
                self._context,
                self._screen_provider,
                window_host_factory=self._window_host_factory,
                warp=self._warp,
                entity_provider=lambda: self._command_entity or self._last_pet_geometry,
                tooltip_requester=self._request_tooltip,
                tooltip_hider=self._hide_tooltip,
            )
            indicator.set_clickthrough(self._clickthrough_enabled)
            self._mic_indicator = indicator
        return indicator

    def _on_mic_stt_state_change(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        self._mic_indicator_window().update_state(data)

    def _speaker_search_window(self) -> DxSpeakerSearchWindow:
        window = self._speaker_search
        if window is None:
            window = DxSpeakerSearchWindow(
                self._context,
                self._screen_provider,
                music_service_provider=self._music_service_provider,
                window_host_factory=self._window_host_factory,
                warp=self._warp,
                tooltip_requester=self._request_tooltip,
                tooltip_hider=self._hide_tooltip,
            )
            window.set_clickthrough(self._clickthrough_enabled)
            self._speaker_search = window
        return window

    def _on_speaker_search_toggle(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        if str(data.get("backend_id") or "") != "directx":
            return
        try:
            instance_id = int(data.get("instance_id", 0))
        except (TypeError, ValueError):
            return
        if instance_id <= 0:
            return
        self._speaker_search_window().toggle(
            WorldObjectInstance("directx", instance_id, "speaker")
        )

    def _on_music_status_change(self, event: Event) -> None:
        if self._speaker_search is not None:
            self._speaker_search.update_music_state(event.data or {})

    def _on_music_login_status_change(self, event: Event) -> None:
        if self._speaker_search is not None:
            self._speaker_search.update_login_state(event.data or {})

    def _panel(
        self,
        panel_id: str,
        *,
        layer: Layer,
        size: tuple[int, int],
        interactive: bool,
        on_submit: Callable[[str], None] | None = None,
        action_text: str = "",
        on_action: Callable[[], None] | None = None,
        on_input_changed: Callable[[str], None] | None = None,
        on_navigation: Callable[[Key | int], str] | None = None,
        on_visibility_changed: Callable[[bool], None] | None = None,
        on_geometry_changed: Callable[[Rect], None] | None = None,
    ) -> _DxPanelWindow:
        panel = self._panels.get(panel_id)
        if panel is None:
            panel = _DxPanelWindow(
                self._context,
                self._screen_provider,
                name=f"DxApplicationUi:{panel_id}",
                layer=layer,
                size=size,
                interactive=interactive,
                window_host_factory=self._window_host_factory,
                warp=self._warp,
                on_submit=on_submit,
                action_text=action_text,
                on_action=on_action,
                on_input_changed=on_input_changed,
                on_navigation=on_navigation,
                on_visibility_changed=on_visibility_changed,
                on_geometry_changed=on_geometry_changed,
                tooltip_text=(
                    application_tooltip_text("command")
                    if panel_id == "command"
                    else ""
                ),
                tooltip_requester=self._request_tooltip,
                tooltip_hider=self._hide_tooltip,
            )
            self._panels[panel_id] = panel
        panel.set_clickthrough(self._clickthrough_enabled)
        return panel

    def _notice_panel(self) -> _DxPanelWindow:
        return self._panel(
            "notice",
            layer=Layer.TOOLTIP,
            size=notice_panel_size(),
            interactive=False,
        )

    def _command_panel(self) -> _DxPanelWindow:
        return self._panel(
            "command",
            layer=Layer.DIALOG,
            size=(
                int(UI.get("cmd_window_width", 240)),
                int(UI.get("cmd_window_height", 36)),
            ),
            interactive=True,
            on_submit=self._publish_command,
            on_input_changed=self._on_command_input_changed,
            on_navigation=self._on_command_navigation,
            on_visibility_changed=self._on_command_visibility_changed,
            on_geometry_changed=self._on_command_geometry_changed,
        )

    def _command_hint_window(self) -> DxCommandHintWindow:
        hint = self._command_hint
        if hint is None:
            hint = DxCommandHintWindow(
                self._context,
                self._screen_provider,
                window_host_factory=self._window_host_factory,
                warp=self._warp,
                on_pick=self._on_command_hint_pick,
                on_execute_hash=self._on_command_hint_execute,
                tooltip_requester=self._request_tooltip,
                tooltip_hider=self._hide_tooltip,
            )
            hint.set_clickthrough(self._clickthrough_enabled)
            self._command_hint = hint
        return hint

    def _on_command_input_changed(self, text: str) -> None:
        hint = self._command_hint
        if hint is not None:
            hint.update_input(text)

    def _on_command_navigation(self, key: Key | int) -> str:
        return self._command_hint_window().handle_navigation(key)

    def _on_command_visibility_changed(self, visible: bool) -> None:
        if visible:
            self._event_center.publish(Event(EventType.TIMER_PAUSE, {
                "source": "command_dialog",
            }))
            panel = self._panels.get("command")
            if panel is not None:
                hint = self._command_hint_window()
                hint.update_input(panel._input)
                hint.show_for(panel._geometry())
                self._action_panel_window().set_command_rect(panel._geometry())
                self._action_panel_window().show()
        else:
            panel = self._panels.get("command")
            if panel is not None and panel.host is not None:
                getter = getattr(panel.host, "get_geometry", None)
                rect = getter() if callable(getter) else getattr(panel.host, "geometry", None)
                if isinstance(rect, Rect):
                    self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
                        "particle_id": "right_fade",
                        "area_type": "rect",
                        "area_data": (
                            rect.x,
                            rect.y,
                            rect.x + rect.width,
                            rect.y + rect.height,
                        ),
                    }))
            self._event_center.publish(Event(EventType.TIMER_RESUME, {
                "source": "command_dialog",
            }))
            if self._command_hint is not None:
                self._command_hint.hide()
            if self._action_panel is not None:
                self._action_panel.hide()

    def _on_command_geometry_changed(self, geometry: Rect) -> None:
        if self._command_hint is not None:
            self._command_hint.set_command_rect(geometry)
        if self._action_panel is not None:
            self._action_panel.set_command_rect(geometry)

    def _on_command_hint_pick(self, text: str) -> None:
        self._command_panel().set_input_text(text)

    def _on_command_hint_execute(self, name: str) -> None:
        value = str(name or "").strip()
        if value:
            self._event_center.publish(Event(EventType.INPUT_HASH, {
                "text": value,
                "raw": f"#{value}",
            }))

    def _qr_panel(self, panel_id: str) -> _DxPanelWindow:
        action_event = (
            EventType.MUSIC_LOGIN_CANCEL_REQUEST
            if panel_id == "music-login"
            else EventType.YUANBAO_LOGIN_QR_HIDE
        )
        return self._panel(
            panel_id,
            layer=Layer.DIALOG,
            size=qr_panel_size(),
            interactive=True,
            action_text=qr_panel_action_text(panel_id),
            on_action=lambda: self._event_center.publish(Event(action_event, {})),
        )

    def _on_information(self, event: Event) -> None:
        text = str((event.data or {}).get("text") or "").strip()
        if text:
            data = event.data or {}
            self._bubble_window().show(
                text,
                int(data.get("min", 40) or 40),
                int(data.get("max", 100) or 100),
                str(data.get("align", "center") or "center"),
                force_replace=bool(data.get("force_replace", False)),
                source=str(data.get("source", "") or ""),
                task_id=str(data.get("task_id", "") or ""),
                kind=str(data.get("kind", "") or ""),
            )

    def _on_bubble_remove(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        self._bubble_window().remove_bubbles(
            source=str(data.get("source", "") or ""),
            task_id=str(data.get("task_id", "") or ""),
            kind=str(data.get("kind", "") or ""),
        )

    def _on_bubble_hide(self, event: Event) -> None:
        if self._bubble is not None:
            self._bubble.clear()
        if self._action_panel is not None:
            self._action_panel.hide()

    def _on_log_error(self, event: Event) -> None:
        data = event.data or {}
        message = str(data.get("message") or data.get("text") or "").strip()
        if message:
            self._notice_panel().show_notice(message, title="运行异常", timeout_ms=8000)

    def _on_command_toggle(self, event: Event) -> None:
        data = event.data or {}
        entity = data.get("entity")
        if entity is not None:
            self._command_entity = entity
        panel = self._command_panel()
        panel.set_anchor_rect(self._command_entity_geometry())
        panel.toggle_command()
        event.mark_handled()

    def _command_entity_geometry(self) -> Rect | None:
        getter = getattr(self._command_entity, "get_core_geometry", None)
        if not callable(getter):
            return None
        try:
            geometry = getter()
        except Exception:
            return None
        return geometry if isinstance(geometry, Rect) else None

    def _on_anchor_response(self, event: Event) -> None:
        data = event.data or {}
        if data.get("window_id") != "pet_window":
            return
        if data.get("ui_id") == "all" and data.get("anchor_id") == "all":
            point = data.get("anchor_point")
            if isinstance(point, Point):
                self._last_pet_geometry = Rect(
                    point.x, point.y,
                    ANIMATION["pet_size"][0], ANIMATION["pet_size"][1],
                )
        panel = self._panels.get("command")
        if panel is not None:
            panel.set_anchor_rect(self._command_entity_geometry())

    def _on_command_open(self, event: Event) -> None:
        self._command_panel().show_command()
        event.mark_handled()

    def _on_command_execute(self, event: Event) -> None:
        command = str((event.data or {}).get("command") or "").strip()
        if command:
            self._event_center.publish(Event(EventType.INPUT_COMMAND, {
                "text": command,
                "raw": f"/{command}",
            }))
        event.mark_handled()

    def _publish_command(self, raw: str) -> None:
        raw = str(raw or "").strip()
        if not raw:
            return
        if raw.startswith("/"):
            event_type = EventType.INPUT_COMMAND
            text = raw[1:].strip()
        elif raw.startswith("#"):
            event_type = EventType.INPUT_HASH
            text = raw[1:].strip()
        else:
            event_type = EventType.INPUT_TEXT
            text = raw
        if text:
            self._event_center.publish(Event(event_type, {"text": text, "raw": raw}))

    def _on_yuanbao_qr(self, event: Event) -> None:
        self._qr_panel("yuanbao-login").show_qr(
            event.data or {},
            default_title="元宝扫码登录",
        )

    def _on_yuanbao_hide(self, event: Event) -> None:
        panel = self._panels.get("yuanbao-login")
        if panel is not None:
            panel.hide()

    def _on_music_qr(self, event: Event) -> None:
        self._qr_panel("music-login").show_qr(
            event.data or {},
            default_title="音乐扫码登录",
        )

    def _on_music_hide(self, event: Event) -> None:
        panel = self._panels.get("music-login")
        if panel is not None:
            panel.hide()

    def start_runtime(self, application: object) -> None:
        if self._started and not self._stopped:
            return
        self.prepare_runtime()
        self._started = True
        self._stopped = False
        self._announcement_window().start()

    def _announcement_window(self) -> DxAnnouncementWindow:
        window = self._announcement
        if window is None:
            window = DxAnnouncementWindow(
                self._context,
                self._screen_provider,
                window_host_factory=self._window_host_factory,
                warp=self._warp,
            )
            self._announcement = window
        return window

    def open_announcement(self) -> None:
        self._announcement_window().open_manual()

    def open_settings(self) -> None:
        if not self._open_workbench_helper("overview"):
            raise RuntimeError('Qt 工作台 helper 启动失败')

    def _open_workbench_helper(self, initial_page: str) -> bool:
        if self._workbench_opener is None:
            return False
        return bool(self._workbench_opener(initial_page))

    def begin_shutdown(self) -> None:
        for panel in tuple(self._panels.values()):
            panel.hide()
        if self._command_hint is not None:
            self._command_hint.hide()
        if self._bubble is not None:
            self._bubble.hide()
        if self._speaker_search is not None:
            self._speaker_search.hide()
        if self._announcement is not None:
            self._announcement.hide()
        if self._mic_indicator is not None:
            self._mic_indicator.hide()
        self._hide_tooltip()

    def stop_runtime(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        for event_type, callback in self._subscriptions:
            self._event_center.unsubscribe(event_type, callback)
        self._subscriptions.clear()
        self._prepared = False
        announcement, self._announcement = self._announcement, None
        if announcement is not None:
            announcement.cleanup()
        game_runtime, self._game_command_runtime = self._game_command_runtime, None
        cleanup = getattr(game_runtime, "cleanup", None)
        if callable(cleanup):
            cleanup()

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self.stop_runtime()
        service, self._yuanbao_service = self._yuanbao_service, None
        configure = getattr(service, "configure_login_dialog_initializer", None)
        if callable(configure):
            configure(None)
        for panel in tuple(self._panels.values()):
            panel.cleanup()
        self._panels.clear()
        hint, self._command_hint = self._command_hint, None
        if hint is not None:
            hint.cleanup()
        bubble, self._bubble = self._bubble, None
        if bubble is not None:
            bubble.cleanup()
        action_panel, self._action_panel = self._action_panel, None
        if action_panel is not None:
            action_panel.cleanup()
        speaker_search, self._speaker_search = self._speaker_search, None
        if speaker_search is not None:
            speaker_search.cleanup()
        mic_indicator, self._mic_indicator = self._mic_indicator, None
        if mic_indicator is not None:
            mic_indicator.cleanup()
        tooltip, self._tooltip = self._tooltip, None
        if tooltip is not None:
            tooltip.cleanup()
        animation, self._animation = self._animation, None
        if animation is not None:
            if callable(self._animation_cleanup):
                self._animation_cleanup()
            else:
                cleanup = getattr(animation, "cleanup", None)
                if callable(cleanup):
                    cleanup()

    def has_exit_animation(self) -> bool:
        return self._animation is not None

    def finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        self.cleanup()


def create_application_ui_host_factory(
    context: DxLoopContext,
    *,
    screen_provider: DxScreenProvider | None = None,
    window_host_factory: Callable[..., DxWindowHost] | None = None,
    warp: bool = False,
) -> Callable[[], ApplicationUiHost]:
    """Bind the shared DirectX desktop services to an application UI factory."""

    def create() -> ApplicationUiHost:
        return DxApplicationUiHost(
            context,
            screen_provider=screen_provider,
            window_host_factory=window_host_factory,
            warp=warp,
        )

    return create


__all__ = ["DxApplicationUiHost", "create_application_ui_host_factory"]
