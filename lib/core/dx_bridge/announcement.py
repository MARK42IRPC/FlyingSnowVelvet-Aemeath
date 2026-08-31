"""Native DirectX announcement window backed by the core announcement service."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path

from lib.core.announcement import AnnouncementDocument, AnnouncementService
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.graphics.announcement_visuals import (
    AnnouncementVisualDescription,
    announcement_hit_test,
    build_announcement_visual,
)
from lib.core.graphics.application_visuals import create_portable_command_hint_metrics
from lib.core.graphics.commands import DrawBatch, scale_batch_alpha
from lib.core.graphics.types import Point, Rect
from lib.core.input.types import Key, MouseButton
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager

from .loop import DxLoopContext
from .opacity import DxOpacityAnimator
from .screen import DxScreenProvider
from .text_metrics import create_directwrite_text_metrics
from .window_host import DxWindowHost


class DxAnnouncementWindow:
    """Present loading, document, paging and suppression without Qt."""

    def __init__(
        self,
        context: DxLoopContext,
        screen_provider: DxScreenProvider,
        *,
        window_host_factory: Callable[..., DxWindowHost],
        warp: bool,
        state_path: Path | None = None,
        cache_path: Path | None = None,
        submit_io: Callable[..., Future] | None = None,
        request_get: Callable[..., object] | None = None,
    ) -> None:
        self._context = context
        self._screen_provider = screen_provider
        self._window_host_factory = window_host_factory
        self._warp = bool(warp)
        self._metrics = create_portable_command_hint_metrics()
        self._host: DxWindowHost | None = None
        self._visible = False
        self._mode = "loading"
        self._document: AnnouncementDocument | None = None
        self._page = 0
        self._hovered = ""
        self._pressed = ""
        self._cleanup_done = False
        self._visual = self._build_visual()
        self._event_center = get_event_center()
        self._opacity = DxOpacityAnimator(context, self._request_repaint)
        self._service = AnnouncementService(
            dispatch=self._context.post,
            on_loading=self.show_loading,
            on_document=self.show_document,
            on_error=self.show_error,
            on_hide=self.hide,
            state_path=state_path,
            cache_path=cache_path,
            submit_io=submit_io,
            request_get=request_get,
        )

    @property
    def host(self) -> DxWindowHost | None:
        return self._host

    @property
    def visual(self) -> AnnouncementVisualDescription:
        return self._visual

    def _build_visual(self) -> AnnouncementVisualDescription:
        return build_announcement_visual(
            self._metrics,
            mode=self._mode,
            document=self._document,
            page=self._page,
            hovered=self._hovered,
            pressed=self._pressed,
            layer=int(Layer.DIALOG),
        )

    def _geometry(self) -> Rect:
        screen = self._screen_provider.get_primary_screen_rect()
        center = Point(screen.x + screen.width / 2.0, screen.y + screen.height / 2.0)
        scale = self._screen_provider.get_scale_for_point(center)
        physical_width = self._visual.size.width * scale
        physical_height = self._visual.size.height * scale
        return Rect(
            screen.x + (screen.width - physical_width) / 2.0,
            screen.y + (screen.height - physical_height) / 2.0,
            self._visual.size.width,
            self._visual.size.height,
        )

    def _ensure_host(self) -> DxWindowHost:
        if self._cleanup_done:
            raise RuntimeError("DX announcement window has been cleaned")
        if self._host is not None:
            return self._host
        geometry = self._geometry()
        host = self._window_host_factory(
            int(geometry.width), int(geometry.height),
            x=int(round(geometry.x)), y=int(round(geometry.y)),
            callbacks=self, warp=self._warp, topmost=True, tool_window=True,
            no_activate=False, clickthrough=False, logical_content=True,
        )
        try:
            self._context.register_poller(host)
            get_layer_manager().register(host, Layer.DIALOG, name="DxAnnouncement")
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
        if self._host is not None:
            self._host.request_repaint()

    def _request_repaint(self) -> None:
        if self._host is not None:
            self._host.request_repaint()

    def _show(self) -> None:
        host = self._ensure_host()
        host.set_geometry(self._geometry())
        host.show()
        self._visible = True
        self._opacity.fade_in()
        host.activate()
        get_layer_manager().enforce_burst()
        host.request_repaint()

    def start(self) -> bool:
        return self._service.start()

    def open_manual(self) -> None:
        self._service.open_manual()

    def show_loading(self) -> None:
        self._mode = "loading"
        self._document = None
        self._page = 0
        self._pressed = ""
        self._refresh()
        self._show()

    def show_document(self, document: AnnouncementDocument, manual: bool) -> None:
        del manual
        self._mode = "document"
        self._document = document
        self._page = 0
        self._pressed = ""
        self._refresh()
        self._show()

    def show_error(self, manual: bool) -> None:
        if not manual:
            return
        self._mode = "error"
        self._document = None
        self._page = 0
        self._pressed = ""
        self._refresh()
        self._show()

    def is_visible(self) -> bool:
        return self._visible

    def hide(self) -> None:
        self._pressed = ""
        if not self._visible:
            return
        self._visible = False
        host = self._host
        if host is not None:
            self._opacity.fade_out(host.hide)

    def _dispatch_action(self, action: str) -> None:
        if action == "close":
            self._service.dismiss()
            self.hide()
        elif action == "retry":
            self._service.retry()
        elif action == "suppress_today":
            self._service.suppress_today()
        elif action == "suppress_forever":
            self._service.suppress_forever()
        elif action == "page_prev" and self._visual.page_count > 1:
            self._page = (self._page - 1) % self._visual.page_count
            self._refresh()
        elif action == "page_next" and self._visual.page_count > 1:
            self._page = (self._page + 1) % self._visual.page_count
            self._refresh()

    def prepare_render(self) -> DrawBatch:
        return scale_batch_alpha(self._visual.batch, self._opacity.value)

    def handle_key_press(self, event: object) -> None:
        key = getattr(event, "key", Key.UNKNOWN)
        if key == Key.ESCAPE:
            self._dispatch_action("close")
        elif key in (Key.LEFT, Key.PAGE_UP, Key.UP):
            self._dispatch_action("page_prev")
        elif key in (Key.RIGHT, Key.PAGE_DOWN, Key.DOWN):
            self._dispatch_action("page_next")

    def handle_key_release(self, event: object) -> None:
        return None

    def handle_pointer_move(self, event: object) -> None:
        pos = getattr(event, "pos", Point())
        hovered = announcement_hit_test(self._visual, pos.x, pos.y)
        if hovered != self._hovered:
            self._hovered = hovered
            self._refresh()

    def handle_pointer_press(self, event: object) -> None:
        if getattr(event, "button", MouseButton.NONE) != MouseButton.LEFT:
            return
        screen_pos = getattr(event, "screen_pos", Point())
        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": "click",
            "area_type": "point",
            "area_data": (screen_pos.x, screen_pos.y),
        }))
        pos = getattr(event, "pos", Point())
        self._hovered = announcement_hit_test(self._visual, pos.x, pos.y)
        self._pressed = self._hovered
        capture = getattr(self._host, "capture_mouse", None)
        if callable(capture):
            capture()
        self._refresh()

    def handle_pointer_release(self, button: MouseButton) -> None:
        action = self._pressed
        commit = bool(action and action == self._hovered and button == MouseButton.LEFT)
        self._pressed = ""
        release = getattr(self._host, "release_mouse", None)
        if callable(release):
            release()
        self._refresh()
        if commit:
            self._dispatch_action(action)

    def handle_pointer_enter(self) -> None:
        return None

    def handle_pointer_leave(self) -> None:
        self._hovered = ""
        self._refresh()

    def handle_window_moved(self, position: Point) -> None:
        return None

    def handle_host_close(self) -> None:
        self._dispatch_action("close")

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._visible = False
        self._opacity.cancel()
        self._service.cleanup()
        host, self._host = self._host, None
        if host is not None:
            get_layer_manager().unregister(host)
            self._context.unregister_poller(host)
            host.cleanup()


__all__ = ["DxAnnouncementWindow"]
