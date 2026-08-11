"""Backend service registry configured by the desktop composition root."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lib.core.application_runtime import ApplicationRuntime
from lib.core.application_ui import ApplicationUiHostFactory
from lib.core.event.pump import EventPumpFactory
from lib.core.graphics.capture import ScreenCapture
from lib.core.graphics.backend import DrawBackend
from lib.core.graphics.types import Point, Rect
from lib.core.overlay_host import OverlayHost
from lib.core.pet_host import PetWindowHost
from lib.core.tray_host import TrayHostFactory
from lib.core.timing.scheduler import Scheduler
from lib.core.window_host import LayerWindowHostFactory, WindowHostFactory


DrawBackendFactory = Callable[[], DrawBackend]
ApplicationRuntimeFactory = Callable[[], ApplicationRuntime]
SchedulerFactory = Callable[[], Scheduler]
ScreenCaptureFactory = Callable[[], ScreenCapture]
PetWindowFactory = Callable[[object, OverlayHost], PetWindowHost]
OverlayFactory = Callable[[], OverlayHost]
DeferredCall = Callable[[int, Callable[[], None]], None]
VirtualScreenProvider = Callable[[], Rect]
ScreenForPointProvider = Callable[[Point | None], Rect]
ScreenCaptureProvider = Callable[[], bytes | None]
BackendCleanup = Callable[[], None]


@dataclass(frozen=True)
class DesktopBackendBundle:
    """Desktop services installed atomically by one backend configurer."""

    draw_backend_factory: DrawBackendFactory
    application_runtime_factory: ApplicationRuntimeFactory
    application_ui_host_factory: ApplicationUiHostFactory
    scheduler_factory: SchedulerFactory
    screen_capture_factory: ScreenCaptureFactory
    pet_window_factory: PetWindowFactory
    particle_overlay_factory: OverlayFactory
    effect_overlay_factory: OverlayFactory
    tray_host_factory: TrayHostFactory
    event_pump_factory: EventPumpFactory
    deferred_call: DeferredCall
    virtual_screen_provider: VirtualScreenProvider
    screen_for_point_provider: ScreenForPointProvider
    layer_window_host_factory: LayerWindowHostFactory
    screen_capture_provider: ScreenCaptureProvider | None = None
    window_host_factory: WindowHostFactory | None = None
    cleanup: BackendCleanup | None = None


_bundle: DesktopBackendBundle | None = None


def configure_desktop_backend(
    *,
    draw_backend_factory: DrawBackendFactory,
    application_runtime_factory: ApplicationRuntimeFactory,
    application_ui_host_factory: ApplicationUiHostFactory,
    scheduler_factory: SchedulerFactory,
    screen_capture_factory: ScreenCaptureFactory,
    pet_window_factory: PetWindowFactory,
    particle_overlay_factory: OverlayFactory,
    effect_overlay_factory: OverlayFactory,
    tray_host_factory: TrayHostFactory,
    event_pump_factory: EventPumpFactory,
    deferred_call: DeferredCall,
    virtual_screen_provider: VirtualScreenProvider,
    screen_for_point_provider: ScreenForPointProvider,
    layer_window_host_factory: LayerWindowHostFactory,
    screen_capture_provider: ScreenCaptureProvider | None = None,
    window_host_factory: WindowHostFactory | None = None,
    cleanup: BackendCleanup | None = None,
) -> None:
    """Install one complete desktop backend before runtime services are created."""
    global _bundle
    _bundle = DesktopBackendBundle(
        draw_backend_factory=draw_backend_factory,
        application_runtime_factory=application_runtime_factory,
        application_ui_host_factory=application_ui_host_factory,
        scheduler_factory=scheduler_factory,
        screen_capture_factory=screen_capture_factory,
        pet_window_factory=pet_window_factory,
        particle_overlay_factory=particle_overlay_factory,
        effect_overlay_factory=effect_overlay_factory,
        tray_host_factory=tray_host_factory,
        event_pump_factory=event_pump_factory,
        deferred_call=deferred_call,
        virtual_screen_provider=virtual_screen_provider,
        screen_for_point_provider=screen_for_point_provider,
        layer_window_host_factory=layer_window_host_factory,
        screen_capture_provider=screen_capture_provider,
        window_host_factory=window_host_factory,
        cleanup=cleanup,
    )


def get_desktop_backend_bundle() -> DesktopBackendBundle | None:
    return _bundle


def get_draw_backend_factory() -> DrawBackendFactory | None:
    return None if _bundle is None else _bundle.draw_backend_factory


def get_application_runtime_factory() -> ApplicationRuntimeFactory | None:
    return None if _bundle is None else _bundle.application_runtime_factory


def get_application_ui_host_factory() -> ApplicationUiHostFactory | None:
    return None if _bundle is None else _bundle.application_ui_host_factory


def get_scheduler_factory() -> SchedulerFactory | None:
    return None if _bundle is None else _bundle.scheduler_factory


def get_screen_capture_factory() -> ScreenCaptureFactory | None:
    return None if _bundle is None else _bundle.screen_capture_factory


def get_pet_window_factory() -> PetWindowFactory | None:
    return None if _bundle is None else _bundle.pet_window_factory


def get_particle_overlay_factory() -> OverlayFactory | None:
    return None if _bundle is None else _bundle.particle_overlay_factory


def get_effect_overlay_factory() -> OverlayFactory | None:
    return None if _bundle is None else _bundle.effect_overlay_factory


def get_tray_host_factory() -> TrayHostFactory | None:
    return None if _bundle is None else _bundle.tray_host_factory


def get_event_pump_factory() -> EventPumpFactory | None:
    return None if _bundle is None else _bundle.event_pump_factory


def get_deferred_call() -> DeferredCall | None:
    return None if _bundle is None else _bundle.deferred_call


def get_virtual_screen_provider() -> VirtualScreenProvider | None:
    return None if _bundle is None else _bundle.virtual_screen_provider


def get_screen_for_point_provider() -> ScreenForPointProvider | None:
    return None if _bundle is None else _bundle.screen_for_point_provider


def get_layer_window_host_factory() -> LayerWindowHostFactory | None:
    return None if _bundle is None else _bundle.layer_window_host_factory


def get_screen_capture_provider() -> ScreenCaptureProvider | None:
    return None if _bundle is None else _bundle.screen_capture_provider


def get_window_host_factory() -> WindowHostFactory | None:
    return None if _bundle is None else _bundle.window_host_factory


def reset_desktop_backend() -> None:
    """Clear backend services for isolated tests."""
    global _bundle
    _bundle = None
