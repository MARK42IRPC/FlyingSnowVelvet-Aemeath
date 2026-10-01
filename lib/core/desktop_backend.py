"""Backend service registry configured by the desktop composition root.

Exactly one backend may be live in a process. The composition root selects a
backend once at startup, so this registry has no reset button: clearing it would
let a second backend install itself on top of a running one, and every host
already built from the first bundle would keep drawing through it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lib.core.application_runtime import ApplicationRuntime
from lib.core.application_ui import ApplicationUiHostFactory
from lib.core.event.pump import EventPumpFactory
from lib.core.render.visuals.capture import ScreenCapture
from lib.core.render.visuals.backend import DrawBackend
from lib.core.render.visuals.types import Point, Rect
from lib.core.logger import get_logger
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
_installation_owner: object | None = None
_logger = get_logger(__name__)


class BackendAlreadyConfiguredError(RuntimeError):
    """Raised when a second backend tries to take over a live process."""


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
    """Install one complete desktop backend before runtime services are created.

    This is the anonymous entry point used by composition roots that install
    themselves once, such as the Qt backend. Owners that may legitimately
    re-register themselves go through :func:`install_desktop_backend_bundle`
    with an ``owner`` token instead.
    """
    bundle = DesktopBackendBundle(
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
    install_desktop_backend_bundle(bundle)


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


def install_desktop_backend_bundle(
    bundle: DesktopBackendBundle,
    *,
    owner: object | None = None,
) -> None:
    """Install the one desktop backend, or refuse to displace the live one.

    Identity, not equality, decides whether a second install is the *same*
    backend coming back. Rebuilt bundles compare unequal even when every factory
    is equivalent (bound-method wrappers are fresh objects each call), so value
    comparison would reject a legitimate re-registration; it is also the wrong
    question, because two genuinely different backends can build equal bundles.
    ``owner`` is the object that owns the bundle and may re-register itself; its
    repeat install is a no-op that keeps the original bundle, so hosts built
    from the first install never end up drawing through a second backend.
    """
    global _bundle, _installation_owner
    if _bundle is None:
        _bundle = bundle
        _installation_owner = owner
        return
    if owner is not None and owner is _installation_owner:
        _logger.debug("桌面后端已由同一 owner 安装，重复安装按空操作处理")
        return
    raise BackendAlreadyConfiguredError(
        "a desktop backend is already active in this process; "
        "the render backend is chosen once at startup and needs a restart"
    )


def uninstall_desktop_backend_bundle(owner: object) -> None:
    """Retract a bundle that the given owner installed and failed to finish.

    This is the rollback half of :func:`install_desktop_backend_bundle`, and it
    is scoped to the installer: an owner can only take back its own install, and
    a backend that never installed anything cannot use this to displace a live
    one. It exists because a half-finished install must leave *nothing* behind —
    otherwise the router's fallback backend would find the registry occupied by
    a backend that has already been torn down and refuse to start, turning a
    recoverable backend failure into a startup failure.
    """
    global _bundle, _installation_owner
    if _bundle is None or _installation_owner is not owner:
        return
    _bundle = None
    _installation_owner = None


__all__ = [
    "BackendAlreadyConfiguredError",
    "DesktopBackendBundle",
    "configure_desktop_backend",
    "get_application_runtime_factory",
    "get_application_ui_host_factory",
    "get_deferred_call",
    "get_desktop_backend_bundle",
    "get_draw_backend_factory",
    "get_effect_overlay_factory",
    "get_event_pump_factory",
    "get_layer_window_host_factory",
    "get_particle_overlay_factory",
    "get_pet_window_factory",
    "get_scheduler_factory",
    "get_screen_capture_factory",
    "get_screen_capture_provider",
    "get_screen_for_point_provider",
    "get_tray_host_factory",
    "get_virtual_screen_provider",
    "get_window_host_factory",
    "install_desktop_backend_bundle",
    "uninstall_desktop_backend_bundle",
]
