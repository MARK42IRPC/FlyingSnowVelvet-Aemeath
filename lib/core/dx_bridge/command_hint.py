"""Qt-free native host for the shared command-hint visual."""
from __future__ import annotations

from collections.abc import Callable

from lib.core.graphics.application_visuals import (
    COMMAND_HINT_DEFAULT_ITEMS,
    COMMAND_HINT_PAGE_SIZE,
    CommandHintVisualDescription,
    application_tooltip_text,
    build_command_hint_visual,
    command_hint_default_pick,
    create_portable_command_hint_metrics,
)
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.graphics.commands import DrawBatch, scale_batch_alpha
from lib.core.graphics.screen import clamp_rect_position
from lib.core.graphics.types import Point, Rect
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.core.input.types import Key, MouseButton
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager

from .loop import DxLoopContext
from .opacity import DxOpacityAnimator
from .screen import DxScreenProvider
from .text_metrics import create_directwrite_text_metrics
from .window_host import DxWindowHost


class DxCommandHintWindow:
    """Execute CommandHintVisualDescription in one interactive DX window."""

    def __init__(
        self,
        context: DxLoopContext,
        screen_provider: DxScreenProvider,
        *,
        window_host_factory: Callable[..., DxWindowHost],
        warp: bool,
        on_pick: Callable[[str], None],
        on_execute_hash: Callable[[str], None],
        tooltip_requester: Callable[[str, Point], None] | None = None,
        tooltip_hider: Callable[[], None] | None = None,
    ) -> None:
        self._context = context
        self._screen_provider = screen_provider
        self._window_host_factory = window_host_factory
        self._warp = bool(warp)
        self._on_pick = on_pick
        self._on_execute_hash = on_execute_hash
        self._tooltip_requester = tooltip_requester
        self._tooltip_hider = tooltip_hider
        self._metrics = create_portable_command_hint_metrics()
        self._mode = "default"
        self._items: tuple[object, ...] = COMMAND_HINT_DEFAULT_ITEMS
        self._selected = 0
        self._page = 0
        self._command_rect = Rect()
        self._visual = self._build_visual()
        self._host: DxWindowHost | None = None
        self._clickthrough = False
        self._cleanup_done = False
        self._event_center = get_event_center()
        self._opacity = DxOpacityAnimator(context, self._request_repaint)

    @property
    def host(self) -> DxWindowHost | None:
        return self._host

    @property
    def visual(self) -> CommandHintVisualDescription:
        return self._visual

    def _build_visual(self) -> CommandHintVisualDescription:
        return build_command_hint_visual(
            self._mode,
            self._items,
            self._selected,
            self._page,
            self._metrics,
            layer=int(Layer.PET_UI),
        )

    def _geometry(self) -> Rect:
        size = self._visual.size
        point = Point(
            self._command_rect.x + self._command_rect.width / 2.0,
            self._command_rect.y + self._command_rect.height / 2.0,
        )
        screen = self._screen_provider.get_screen_rect_for_point(point)
        scale = self._screen_provider.get_scale_for_point(point)
        x, y, _ = clamp_rect_position(
            int(round(self._command_rect.x)),
            int(round(self._command_rect.y + self._command_rect.height + 2 * scale)),
            int(round(size.width * scale)),
            int(round(size.height * scale)),
            screen,
        )
        return Rect(x, y, size.width, size.height)

    def _ensure_host(self) -> DxWindowHost:
        if self._cleanup_done:
            raise RuntimeError("DX command hint has been cleaned")
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
        try:
            self._context.register_poller(host)
            get_layer_manager().register(host, Layer.PET_UI, name="DxCommandHint")
        except Exception:
            self._context.unregister_poller(host)
            host.cleanup()
            raise
        self._host = host
        native_metrics = create_directwrite_text_metrics(host)
        if native_metrics is not None:
            self._metrics = native_metrics
            self._visual = self._build_visual()
            host.set_geometry(self._geometry())
        return host

    def _refresh(self) -> None:
        self._visual = self._build_visual()
        host = self._host
        if host is not None:
            host.set_geometry(self._geometry())
            host.request_repaint()

    def _request_repaint(self) -> None:
        if self._host is not None:
            self._host.request_repaint()

    def set_command_rect(self, rect: Rect) -> None:
        self._command_rect = rect
        if self._host is not None:
            self._host.set_geometry(self._geometry())

    def show_for(self, rect: Rect) -> None:
        self.set_command_rect(rect)
        host = self._ensure_host()
        host.set_geometry(self._geometry())
        if not host.is_visible():
            host.show()
        self._opacity.fade_in()
        get_layer_manager().enforce_burst()
        host.request_repaint()

    def hide(self) -> None:
        host = self._host
        if host is None or not host.is_visible():
            return
        geometry = host.get_geometry()
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": "right_fade",
            "area_type": "rect",
            "area_data": (
                geometry.x, geometry.y,
                geometry.x + geometry.width, geometry.y + geometry.height,
            ),
        }))
        self._opacity.fade_out(host.hide)

    def set_clickthrough(self, enabled: bool) -> None:
        self._clickthrough = bool(enabled)
        if self._host is not None:
            setter = getattr(self._host, "set_clickthrough", None)
            if callable(setter):
                setter(self._clickthrough)

    def update_input(self, text: str) -> None:
        value = str(text or "")
        if value.startswith("#"):
            self._mode = "hash"
            self._items = tuple(get_hash_cmd_registry().filter(value[1:]))
        else:
            self._mode = "default"
            self._items = COMMAND_HINT_DEFAULT_ITEMS
        self._selected = 0 if self._items else -1
        self._page = 0
        self._refresh()

    def _page_items(self) -> tuple[object, ...]:
        start = self._page * COMMAND_HINT_PAGE_SIZE
        return self._items[start:start + COMMAND_HINT_PAGE_SIZE]

    def _turn_page(self, direction: int) -> None:
        if self._mode != "hash" or len(self._items) <= COMMAND_HINT_PAGE_SIZE:
            return
        max_page = (len(self._items) - 1) // COMMAND_HINT_PAGE_SIZE
        self._page = (self._page + int(direction)) % (max_page + 1)
        self._selected = 0
        self._refresh()

    def handle_navigation(self, key: Key | int) -> str:
        if key == Key.TAB and self._mode == "hash":
            items = self._page_items()
            if 0 <= self._selected < len(items):
                return f"#{items[self._selected][0]} "
        elif key in (Key.UP, Key.DOWN) and self._mode == "hash":
            items = self._page_items()
            if items:
                direction = -1 if key == Key.UP else 1
                self._selected = max(0, min(len(items) - 1, self._selected + direction))
                self._refresh()
        elif key == Key.LEFT:
            self._turn_page(-1)
        elif key == Key.RIGHT:
            self._turn_page(1)
        return ""

    def prepare_render(self) -> DrawBatch:
        return scale_batch_alpha(self._visual.batch, self._opacity.value)

    @staticmethod
    def _contains(rect: Rect, point: Point) -> bool:
        return (
            rect.x <= point.x < rect.x + rect.width
            and rect.y <= point.y < rect.y + rect.height
        )

    def _row_at(self, point: Point) -> int:
        for index, rect in enumerate(self._visual.row_rects):
            if self._contains(rect, point):
                return index
        return -1

    def handle_pointer_move(self, event: object) -> None:
        row = self._row_at(getattr(event, "pos", Point()))
        page_items = self._page_items()
        if self._mode == "default" or 0 <= row < len(page_items):
            if row != self._selected:
                self._selected = row
                self._refresh()
        if callable(self._tooltip_requester):
            self._tooltip_requester(
                application_tooltip_text("command_hint"),
                getattr(event, "screen_pos", Point()),
            )

    def handle_pointer_press(self, event: object) -> None:
        if getattr(event, "button", MouseButton.NONE) != MouseButton.LEFT:
            return
        screen_pos = getattr(event, "screen_pos", Point())
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": "click",
            "area_type": "point",
            "area_data": (screen_pos.x, screen_pos.y),
        }))
        point = getattr(event, "pos", Point())
        row = self._row_at(point)
        if self._mode == "default":
            value = command_hint_default_pick(row)
            if value:
                self._on_pick(value)
            return
        page_items = self._page_items()
        if 0 <= row < len(page_items):
            self._on_execute_hash(str(page_items[row][0]))
            return
        page_rect = self._visual.page_indicator_rect
        if page_rect is not None and self._contains(page_rect, point):
            self._turn_page(-1 if point.x < self._visual.size.width / 2.0 else 1)

    def handle_pointer_enter(self) -> None:
        return None

    def handle_pointer_leave(self) -> None:
        if callable(self._tooltip_hider):
            self._tooltip_hider()

    def handle_pointer_release(self, button: MouseButton) -> None:
        return None

    def handle_key_press(self, event: object) -> None:
        return None

    def handle_key_release(self, event: object) -> None:
        return None

    def handle_window_moved(self, position: Point) -> None:
        return None

    def handle_host_close(self) -> None:
        self.hide()

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._opacity.cancel()
        host, self._host = self._host, None
        if host is None:
            return
        try:
            get_layer_manager().unregister(host)
        finally:
            self._context.unregister_poller(host)
        host.cleanup()


__all__ = ["DxCommandHintWindow"]
