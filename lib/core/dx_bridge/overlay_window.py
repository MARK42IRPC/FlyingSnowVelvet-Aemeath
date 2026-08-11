"""Transparent DirectComposition window used by DX visual overlays."""
from __future__ import annotations

from collections.abc import Callable

from lib.core.graphics.commands import DrawBatch
from lib.core.graphics.types import Rect
from lib.core.layer import Layer
from lib.core.layer_manager import get_layer_manager

from .loop import DxLoopContext
from .screen import DxScreenProvider
from .window_host import DxWindowHost


class DxOverlayWindow:
    """Own a click-through DX window and its latest immutable draw batch."""

    def __init__(
        self,
        context: DxLoopContext,
        layer: Layer,
        *,
        name: str,
        screen_provider: DxScreenProvider | None = None,
        window_host_factory: Callable[..., DxWindowHost] | None = None,
        warp: bool = False,
    ) -> None:
        self._context = context
        self._layer = layer
        self._name = str(name)
        self._screen_provider = screen_provider or DxScreenProvider()
        self._window_host_factory = window_host_factory or DxWindowHost
        self._warp = bool(warp)
        self._batch = DrawBatch()
        self._cleanup_done = False
        self._registered = False
        self._host: DxWindowHost | None = None
        self._create_host()

    @property
    def window_host(self) -> DxWindowHost | None:
        return self._host

    @property
    def geometry(self) -> Rect:
        host = self._host
        return host.get_geometry() if host is not None else Rect()

    def _create_host(self) -> None:
        geometry = self._screen_provider.get_virtual_screen_rect()
        width = max(1, int(round(geometry.width)))
        height = max(1, int(round(geometry.height)))
        host = self._window_host_factory(
            width,
            height,
            x=int(round(geometry.x)),
            y=int(round(geometry.y)),
            callbacks=self,
            warp=self._warp,
            topmost=True,
            tool_window=True,
            no_activate=True,
            clickthrough=True,
        )
        self._host = host
        try:
            self._context.register_poller(host)
            get_layer_manager().register(host, self._layer, name=self._name)
            self._registered = True
        except Exception:
            self._context.unregister_poller(host)
            host.cleanup()
            self._host = None
            raise

    def refresh_geometry(self) -> None:
        host = self._host
        if host is None or host.is_visible():
            return
        geometry = self._screen_provider.get_virtual_screen_rect()
        host.set_geometry(Rect(
            geometry.x,
            geometry.y,
            max(1, geometry.width),
            max(1, geometry.height),
        ))

    def prepare_render(self) -> DrawBatch:
        return self._batch

    # Overlay windows are deliberately click-through, but still satisfy the
    # host callback surface so native move/repaint messages are harmless.
    def handle_pointer_enter(self) -> None:
        return None

    def handle_pointer_leave(self) -> None:
        return None

    def handle_pointer_press(self, event: object) -> None:
        return None

    def handle_pointer_move(self, event: object) -> None:
        return None

    def handle_pointer_release(self, button: object) -> None:
        return None

    def handle_window_moved(self, position: object) -> None:
        return None

    def handle_key_press(self, event: object) -> None:
        return None

    def handle_key_release(self, event: object) -> None:
        return None

    def handle_host_close(self) -> None:
        self.cleanup()

    def submit(self, batch: DrawBatch) -> None:
        if not isinstance(batch, DrawBatch):
            raise TypeError("DX overlay requires a DrawBatch")
        host = self._host
        if host is None or self._cleanup_done:
            return
        self._batch = batch
        if batch.commands:
            if not host.is_visible():
                self.refresh_geometry()
                host.show()
                get_layer_manager().enforce_burst()
            host.request_repaint()
            return
        if host.is_visible():
            host.render_batch(batch)
            host.hide()

    def flush_immediately(self) -> None:
        self.submit(DrawBatch())

    def cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True
        host, self._host = self._host, None
        if host is None:
            return
        self._batch = DrawBatch()
        if self._registered:
            try:
                get_layer_manager().unregister(host)
            finally:
                self._context.unregister_poller(host)
            self._registered = False
        host.cleanup()


__all__ = ["DxOverlayWindow"]
