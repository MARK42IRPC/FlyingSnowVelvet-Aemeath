"""Qt-free application UI hosted by native DirectX windows."""
from __future__ import annotations

import webbrowser
from collections.abc import Callable

from config.config import ANIMATION, BUBBLE_CONFIG, COMMAND_DIALOG, UI
from lib.core.application_ui import ApplicationUiHost
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.graphics.application_visuals import (
    BubbleVisualDescription,
    build_bubble_visual,
    build_command_action_panel_visual,
    build_notice_panel_visual,
    build_qr_panel_visual,
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
from lib.core.graphics.commands import DrawBatch
from lib.core.graphics.resources import ImageResource
from lib.core.graphics.types import Point, Rect
from lib.core.graphics.visuals import (
    build_command_panel_batch,
    resolve_command_panel_geometry,
)
from lib.core.input.types import Key, MouseButton
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager
from lib.core.logger import get_logger

from .loop import DxLoopContext, DxScheduledCall
from .command_hint import DxCommandHintWindow
from .screen import DxScreenProvider
from .window_host import DxWindowHost


_logger = get_logger(__name__)
_ANNOUNCEMENT_URL = (
    "https://gitee.com/Mark42IRPC/Aemeath-AIdeskpet/releases/download/RESC/"
    "%E5%85%AC%E5%91%8A.txt"
)


class _DxBubbleWindow:
    """Native host for the Qt-baseline chat bubble presenter."""

    def __init__(self, context, screen_provider, *, window_host_factory, warp, entity_provider):
        self._context = context
        self._screen_provider = screen_provider
        self._window_host_factory = window_host_factory
        self._warp = bool(warp)
        self._entity_provider = entity_provider
        self._metrics = create_portable_bubble_text_metrics()
        self._host = None
        self._description: BubbleVisualDescription | None = None
        self._current: tuple[str, int, int, str] | None = None
        self._queue: list[tuple[str, int, int, str]] = []
        self._elapsed = 0
        self._hide_call = None
        self._cleanup_done = False

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
        screen = self._screen_provider.get_screen_rect_for_point(anchor)
        geometry = resolve_bubble_geometry(anchor, description.size, screen)
        host = self._window_host_factory(
            int(geometry.width), int(geometry.height),
            x=int(geometry.x), y=int(geometry.y), callbacks=self,
            warp=self._warp, topmost=True, tool_window=True,
            no_activate=True, clickthrough=False,
        )
        self._context.register_poller(host)
        get_layer_manager().register(host, Layer.PET_UI, name="DxBubble")
        self._host = host
        return host

    def _show_current(self):
        if self._current is None:
            return
        text, _min_ticks, max_ticks, align = self._current
        self._description = self._build(text, align)
        host = self._ensure_host(self._description)
        anchor = self._pet_anchor()
        screen = self._screen_provider.get_screen_rect_for_point(anchor)
        host.set_geometry(resolve_bubble_geometry(anchor, self._description.size, screen))
        host.show()
        host.request_repaint()
        self._elapsed = 0
        self._cancel_hide()
        self._hide_call = self._context.call_later(max(1, int(max_ticks)) * 50, self.hide)

    def show(self, text: str, min_ticks: int = 40, max_ticks: int = 100, align: str = "center"):
        item = (str(text or ""), max(0, int(min_ticks)), max(1, int(max_ticks)), str(align or "center"))
        if not item[0]:
            return
        if self._current is None:
            self._current = item
            self._show_current()
        elif self._elapsed >= self._current[1]:
            self._current = item
            self._show_current()
        else:
            self._queue.append(item)

    def hide(self):
        self._cancel_hide()
        if self._host is not None:
            self._host.hide()
        self._current = None
        self._description = None
        if self._queue:
            item = self._queue.pop(0)
            self._current = item
            self._show_current()

    def tick(self):
        if self._current is not None:
            self._elapsed += 1

    def prepare_render(self) -> DrawBatch:
        return self._description.batch if self._description is not None else DrawBatch()

    def set_anchor_entity(self, entity):
        if self._host is None or not self._host.is_visible() or self._description is None:
            return
        anchor = self._pet_anchor()
        screen = self._screen_provider.get_screen_rect_for_point(anchor)
        self._host.set_geometry(resolve_bubble_geometry(anchor, self._description.size, screen))

    def handle_pointer_press(self, event):
        button = getattr(event, "button", MouseButton.NONE)
        if button == MouseButton.LEFT:
            self.hide()
        elif button == MouseButton.RIGHT:
            self.hide()

    def handle_pointer_release(self, button):
        return None
    def handle_pointer_enter(self):
        return None
    def handle_pointer_leave(self):
        return None
    def handle_pointer_move(self, event):
        return None
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

    def cleanup(self):
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._cancel_hide()
        host, self._host = self._host, None
        if host is not None:
            get_layer_manager().unregister(host)
            self._context.unregister_poller(host)
            host.cleanup()


class _DxCommandActionPanel:
    """One native window executing the shared seven-button action batch."""

    def __init__(self, context, screen_provider, *, window_host_factory, warp, on_action):
        self._context = context
        self._screen_provider = screen_provider
        self._window_host_factory = window_host_factory
        self._warp = bool(warp)
        self._on_action = on_action
        self._host = None
        self._command_rect: Rect | None = None
        self._hovered = ""
        self._pressed = ""
        self._batch = DrawBatch()
        self._cleanup_done = False

    @property
    def host(self):
        return self._host

    def _layout(self):
        return resolve_command_action_panel_layout(self._command_rect or Rect())

    def _rebuild(self):
        if self._command_rect is None:
            self._batch = DrawBatch()
            return
        visual = build_command_action_panel_visual(
            self._command_rect,
            hovered=self._hovered,
            pressed=self._pressed,
        )
        self._batch = visual.batch

    def _ensure_host(self):
        if self._host is not None:
            return self._host
        layout = self._layout()
        origin_x = min(rect.x for _name, rect in layout.rects)
        origin_y = min(rect.y for _name, rect in layout.rects)
        host = self._window_host_factory(
            int(layout.size.width), int(layout.size.height),
            x=int(origin_x), y=int(origin_y), callbacks=self,
            warp=self._warp, topmost=True, tool_window=True,
            no_activate=False, clickthrough=False,
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
            origin_x = min(item.x for _name, item in layout.rects)
            origin_y = min(item.y for _name, item in layout.rects)
            self._host.set_geometry(Rect(origin_x, origin_y, layout.size.width, layout.size.height))
            self._host.request_repaint()

    def show(self):
        if self._command_rect is None:
            return
        self._rebuild()
        host = self._ensure_host()
        layout = self._layout()
        origin_x = min(item.x for _name, item in layout.rects)
        origin_y = min(item.y for _name, item in layout.rects)
        host.set_geometry(Rect(origin_x, origin_y, layout.size.width, layout.size.height))
        host.show()
        host.request_repaint()

    def hide(self):
        self._hovered = ""
        self._pressed = ""
        if self._host is not None:
            self._host.hide()

    def prepare_render(self):
        return self._batch

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

    def handle_pointer_press(self, event):
        if getattr(event, "button", MouseButton.NONE) != MouseButton.LEFT:
            return
        pos = getattr(event, "global_pos", getattr(event, "screen_pos", Point()))
        self._pressed = self._hit(pos)
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
        self._anchor_rect: Rect | None = None
        self._action_hovered = False
        self._action_pressed = False

    @property
    def host(self) -> DxWindowHost | None:
        return self._host

    def _geometry(self) -> Rect:
        screen = self._screen_provider.get_primary_screen_rect()
        width, height = self._size
        if self._interactive and self._anchor_rect is not None:
            anchor = self._anchor_rect
            screen = self._screen_provider.get_screen_rect_for_point(Point(
                anchor.x + anchor.width / 2.0,
                anchor.y + anchor.height / 2.0,
            ))
            return resolve_command_panel_geometry(
                anchor,
                self._size,
                screen,
                offset_x=float(COMMAND_DIALOG.get("offset_x", 6)),
                offset_y=float(COMMAND_DIALOG.get("offset_y", 0)),
            )
        if self._interactive:
            x = screen.x + (screen.width - width) / 2.0
            y = screen.y + (screen.height - height) / 2.0
        else:
            x = screen.x + screen.width - width - 24
            y = screen.y + screen.height - height - 48
        return Rect(x, y, width, height)

    def set_anchor_rect(self, geometry: Rect | None) -> None:
        self._anchor_rect = geometry if isinstance(geometry, Rect) else None
        if self.is_visible() and self._host is not None:
            resolved = self._geometry()
            self._host.set_geometry(resolved)
            if callable(self._on_geometry_changed):
                self._on_geometry_changed(resolved)

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
            clickthrough=not self._interactive,
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
        return self._host is not None and self._host.is_visible()

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
        get_layer_manager().enforce_burst()
        host.request_repaint()
        if callable(self._on_geometry_changed):
            self._on_geometry_changed(geometry)
        if callable(self._on_visibility_changed):
            self._on_visibility_changed(True)

    def hide(self) -> None:
        self._cancel_hide()
        self._composition = ""
        if self._host is not None:
            self._host.hide()
        if callable(self._on_visibility_changed):
            self._on_visibility_changed(False)

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
        return self._batch

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
        if self._surface_kind == "qr":
            self._action_hovered = False
            if not self._action_pressed:
                self._rebuild_batch()
                if self._host is not None:
                    self._host.request_repaint()

    def handle_pointer_move(self, event: object) -> None:
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
        self._cancel_hide()
        host, self._host = self._host, None
        if host is None:
            return
        try:
            get_layer_manager().unregister(host)
        finally:
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
    ) -> None:
        self._context = context
        self._screen_provider = screen_provider or DxScreenProvider()
        self._window_host_factory = window_host_factory or DxWindowHost
        self._warp = bool(warp)
        self._announcement_opener = announcement_opener or (
            lambda url: webbrowser.open(url, new=2)
        )
        self._event_center = get_event_center()
        self._yuanbao_service: object | None = None
        self._panels: dict[str, _DxPanelWindow] = {}
        self._bubble: _DxBubbleWindow | None = None
        self._action_panel: _DxCommandActionPanel | None = None
        self._command_hint: DxCommandHintWindow | None = None
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

    def configure_services(self, yuanbao_service: object) -> None:
        self._yuanbao_service = yuanbao_service
        configure = getattr(yuanbao_service, "configure_login_dialog_initializer", None)
        if callable(configure):
            configure(self.prepare_runtime)

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
        self._subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_state)
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

    def _bubble_window(self) -> _DxBubbleWindow:
        bubble = self._bubble
        if bubble is None:
            bubble = _DxBubbleWindow(
                self._context,
                self._screen_provider,
                window_host_factory=self._window_host_factory,
                warp=self._warp,
                entity_provider=lambda: self._command_entity or self._last_pet_geometry,
            )
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
            )
            self._action_panel = panel
        return panel

    def _on_command_action(self, action: str) -> None:
        event_map = {
            "close": EventType.APP_QUIT,
        }
        if action == "clickthrough":
            self._clickthrough_enabled = not self._clickthrough_enabled
            self._event_center.publish(Event(EventType.UI_CLICKTHROUGH_TOGGLE, {
                "enabled": self._clickthrough_enabled,
                "source": "dx_command_action",
            }))
            return
        if action == "chat_mode":
            self._chat_listening = not self._chat_listening
            self._event_center.publish(Event(
                EventType.MIC_STT_START if self._chat_listening else EventType.MIC_STT_STOP,
                {
                    "source": "chat_mode_button",
                    "auto_mode": False,
                    "auto_submit": True,
                    "emit_partial": True,
                },
            ))
            return
        event_type = event_map.get(action)
        if event_type is None:
            return
        payload = {"source": "dx_command_action", "action": action}
        self._event_center.publish(Event(event_type, payload))

    def _on_clickthrough_state(self, event: Event) -> None:
        enabled = bool((event.data or {}).get("enabled", False))
        self._clickthrough_enabled = enabled
        if self._action_panel is not None and self._action_panel.host is not None:
            setter = getattr(self._action_panel.host, "set_clickthrough", None)
            if callable(setter):
                setter(enabled)

    def _on_tick(self, event: Event) -> None:
        if self._bubble is not None:
            self._bubble.tick()
            self._bubble.set_anchor_entity(self._command_entity)

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
            )
            self._panels[panel_id] = panel
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
            )
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
            panel = self._panels.get("command")
            if panel is not None:
                hint = self._command_hint_window()
                hint.update_input(panel._input)
                hint.show_for(panel._geometry())
                self._action_panel_window().set_command_rect(panel._geometry())
                self._action_panel_window().show()
        elif self._command_hint is not None:
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
            )

    def _on_bubble_hide(self, event: Event) -> None:
        if self._bubble is not None:
            self._bubble.hide()
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
            event_type = EventType.INPUT_CHAT
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
        self.prepare_runtime()
        self._started = True
        self._stopped = False

    def open_announcement(self) -> None:
        try:
            opened = self._announcement_opener(_ANNOUNCEMENT_URL)
        except Exception as exc:
            _logger.error("DX announcement open failed: %s", exc)
            opened = False
        if opened is False:
            self._notice_panel().show_notice(
                "公告页面打开失败，请稍后重试。",
                title="桌宠公告",
            )

    def open_settings(self) -> None:
        from lib.script.app.workbench_helper import launch_workbench_helper

        if not launch_workbench_helper():
            raise RuntimeError('Qt 工作台 helper 启动失败')

    def begin_shutdown(self) -> None:
        for panel in tuple(self._panels.values()):
            panel.hide()
        if self._command_hint is not None:
            self._command_hint.hide()
        if self._bubble is not None:
            self._bubble.hide()

    def stop_runtime(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        for event_type, callback in self._subscriptions:
            self._event_center.unsubscribe(event_type, callback)
        self._subscriptions.clear()
        self._prepared = False

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

    def has_exit_animation(self) -> bool:
        return False

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
