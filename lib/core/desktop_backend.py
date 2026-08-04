"""Backend service registry configured by the desktop composition root."""
from __future__ import annotations

from collections.abc import Callable

from lib.core.event.pump import EventPumpFactory
from lib.core.graphics.backend import DrawBackend
from lib.core.graphics.types import Point, Rect


DrawBackendFactory = Callable[[], DrawBackend]
DeferredCall = Callable[[int, Callable[[], None]], None]
VirtualScreenProvider = Callable[[], Rect]
ScreenForPointProvider = Callable[[Point | None], Rect]
ScreenCaptureProvider = Callable[[], bytes | None]

_draw_backend_factory: DrawBackendFactory | None = None
_event_pump_factory: EventPumpFactory | None = None
_deferred_call: DeferredCall | None = None
_virtual_screen_provider: VirtualScreenProvider | None = None
_screen_for_point_provider: ScreenForPointProvider | None = None
_screen_capture_provider: ScreenCaptureProvider | None = None


def configure_desktop_backend(
    *,
    draw_backend_factory: DrawBackendFactory,
    event_pump_factory: EventPumpFactory,
    deferred_call: DeferredCall,
    virtual_screen_provider: VirtualScreenProvider,
    screen_for_point_provider: ScreenForPointProvider,
    screen_capture_provider: ScreenCaptureProvider | None = None,
) -> None:
    """Install one complete desktop backend before runtime services are created."""
    global _draw_backend_factory
    global _event_pump_factory
    global _deferred_call
    global _virtual_screen_provider
    global _screen_for_point_provider
    global _screen_capture_provider

    _draw_backend_factory = draw_backend_factory
    _event_pump_factory = event_pump_factory
    _deferred_call = deferred_call
    _virtual_screen_provider = virtual_screen_provider
    _screen_for_point_provider = screen_for_point_provider
    _screen_capture_provider = screen_capture_provider


def get_draw_backend_factory() -> DrawBackendFactory | None:
    return _draw_backend_factory


def get_event_pump_factory() -> EventPumpFactory | None:
    return _event_pump_factory


def get_deferred_call() -> DeferredCall | None:
    return _deferred_call


def get_virtual_screen_provider() -> VirtualScreenProvider | None:
    return _virtual_screen_provider


def get_screen_for_point_provider() -> ScreenForPointProvider | None:
    return _screen_for_point_provider


def get_screen_capture_provider() -> ScreenCaptureProvider | None:
    return _screen_capture_provider


def reset_desktop_backend() -> None:
    """Clear backend services for isolated tests."""
    global _draw_backend_factory
    global _event_pump_factory
    global _deferred_call
    global _virtual_screen_provider
    global _screen_for_point_provider
    global _screen_capture_provider

    _draw_backend_factory = None
    _event_pump_factory = None
    _deferred_call = None
    _virtual_screen_provider = None
    _screen_for_point_provider = None
    _screen_capture_provider = None
