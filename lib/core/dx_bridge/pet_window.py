"""Diagnostic composition of the pure pet controller and DX window host."""
from __future__ import annotations

from collections.abc import Callable

from config.config import ANIMATION
from lib.core.event.center import Event, EventType
from lib.core.graphics.anchors import get_anchor_point as get_rect_anchor_point
from lib.core.graphics.types import Point, Rect
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager
from lib.core.pet_window import PetWindow

from .loop import DxLoopContext
from .scheduler import DxScheduler
from .screen import DxScreenProvider, get_cursor_position
from .window_host import DxWindowHost


class DxPetWindow(PetWindow):
    """Drive the backend-neutral pet controller through a native DX host."""

    def __init__(
        self,
        gifs: dict,
        particle_overlay: object,
        *,
        context: DxLoopContext,
        screen_provider: DxScreenProvider | None = None,
        window_host_factory: Callable[..., DxWindowHost] | None = None,
        cursor_position_provider: Callable[[], Point] | None = None,
        shutdown_ui: Callable[[], None] | None = None,
    ) -> None:
        self._dx_context = context
        self._dx_screen_provider = screen_provider or DxScreenProvider()
        self._dx_window_host_factory = window_host_factory or DxWindowHost
        self._dx_cursor_position_provider = cursor_position_provider or get_cursor_position
        self._dx_shutdown_ui = shutdown_ui
        self._dx_window_host: DxWindowHost | None = None
        self._dx_on_close: Callable[[], None] | None = None
        self._dx_close_requested = False
        self._dx_shutdown_done = False
        try:
            super().__init__(gifs, particle_overlay)
        except Exception:
            if hasattr(self, "_movement"):
                try:
                    self.cleanup_core_state()
                except Exception:
                    pass
            self._cleanup_window_host()
            raise

    @property
    def window_host(self) -> DxWindowHost | None:
        return self._dx_window_host

    def _require_window_host(self) -> DxWindowHost:
        host = self._dx_window_host
        if host is None:
            raise RuntimeError("DX pet window host has not been created")
        return host

    def _host_create_scheduler(self):
        return DxScheduler(self._dx_context)

    def _host_cursor_position(self) -> Point:
        return self._dx_cursor_position_provider()

    def _host_setup(self, on_close) -> None:
        width, height = ANIMATION["pet_size"]
        screen = self._dx_screen_provider.get_primary_screen_rect()
        x = int(round(screen.x + (screen.width - width) / 2))
        y = int(round(screen.y + (screen.height - height) / 2))
        host = self._dx_window_host_factory(
            width,
            height,
            x=x,
            y=y,
            callbacks=self,
            topmost=True,
            tool_window=True,
            no_activate=False,
            clickthrough=False,
        )
        self._dx_window_host = host
        self._dx_on_close = on_close
        try:
            self._dx_context.register_poller(host)
        except Exception:
            host.cleanup()
            self._dx_window_host = None
            raise

    def _host_finalize_startup(self) -> None:
        host = self._require_window_host()
        layer_manager = get_layer_manager()
        layer_manager.register(host, Layer.MAIN_PET, name="DxPetWindow")
        host.show()
        layer_manager.enforce_burst()
        self._startup_voice_sound.play()
        self._move_particle_last_pos = self.get_core_position()
        self._move_particle_enabled = True
        host.request_repaint()

    def get_position(self) -> Point:
        return self.get_core_position()

    def get_core_position(self) -> Point:
        host = self._dx_window_host
        if host is None:
            return Point()
        geometry = host.get_geometry()
        return Point(geometry.x, geometry.y)

    def get_geometry(self) -> Rect:
        return self.get_core_geometry()

    def get_core_geometry(self) -> Rect:
        host = self._dx_window_host
        return host.get_geometry() if host is not None else Rect()

    def get_anchor_point(self, anchor_id: str) -> Point:
        geometry = self.get_core_geometry()
        return get_rect_anchor_point(
            Rect(0, 0, geometry.width, geometry.height),
            anchor_id,
        )

    def _host_publish_anchor_response(self, **kwargs) -> None:
        geometry = self.get_core_geometry()
        anchor_id = str(kwargs.get("anchor_id") or "center")
        local_anchor = self.get_anchor_point(anchor_id)
        self._event_center.publish(
            Event(
                EventType.UI_ANCHOR_RESPONSE,
                {
                    "window_id": kwargs.get("window_id"),
                    "anchor_id": anchor_id,
                    "anchor_point": Point(
                        geometry.x + local_anchor.x,
                        geometry.y + local_anchor.y,
                    ),
                    "ui_id": kwargs.get("ui_id"),
                },
            )
        )

    def _host_set_clickthrough(self, enabled: bool) -> None:
        host = self._dx_window_host
        if host is not None:
            host.set_clickthrough(enabled)

    def _host_toggle_command_dialog(self) -> None:
        self._event_center.publish(
            Event(EventType.UI_COMMAND_TOGGLE, {"entity": self})
        )

    def _host_move(self, position: Point) -> None:
        host = self._dx_window_host
        if host is None or not host.is_alive():
            return
        geometry = host.get_geometry()
        host.set_geometry(
            Rect(position.x, position.y, geometry.width, geometry.height)
        )

    def _host_request_repaint(self) -> None:
        host = self._dx_window_host
        if host is not None:
            host.request_repaint()

    def _host_shutdown_ui(self) -> None:
        if self._dx_shutdown_ui is not None:
            self._dx_shutdown_ui()

    def handle_host_close(self) -> None:
        if self._dx_close_requested:
            return
        self._dx_close_requested = True
        super().handle_host_close()
        if self._dx_on_close is not None:
            self._dx_on_close()

    def _cleanup_window_host(self) -> None:
        host = self._dx_window_host
        if host is None:
            return
        try:
            get_layer_manager().unregister(host)
        except Exception:
            pass
        self._dx_context.unregister_poller(host)
        try:
            host.cleanup()
        except Exception:
            pass

    def shutdown_host(self) -> None:
        if self._dx_shutdown_done:
            return
        self._dx_shutdown_done = True
        self.cleanup_core_state()
        self._cleanup_window_host()


def create_pet_window_factory(
    context: DxLoopContext,
    *,
    screen_provider: DxScreenProvider | None = None,
    window_host_factory: Callable[..., DxWindowHost] | None = None,
) -> Callable[[dict, object], DxPetWindow]:
    """Bind shared DX services to the two-argument desktop bundle factory."""

    def create(gifs: dict, particle_overlay: object) -> DxPetWindow:
        return DxPetWindow(
            gifs,
            particle_overlay,
            context=context,
            screen_provider=screen_provider,
            window_host_factory=window_host_factory,
        )

    return create


__all__ = ["DxPetWindow", "create_pet_window_factory"]
