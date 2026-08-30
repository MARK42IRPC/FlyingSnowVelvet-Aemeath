"""DirectX desktop composition with one owned loop and cleanup boundary."""
from __future__ import annotations

import threading
from collections.abc import Callable

from lib.core.desktop_backend import DesktopBackendBundle, configure_desktop_backend
from lib.core.world_objects import (
    configure_world_object_backend,
    get_world_object_backend,
    reset_world_object_backend,
)

from .application_runtime import DxApplicationRuntime
from .application_ui import DxApplicationUiHost
from .effect_system import create_effect_overlay_factory
from .event_pump import DxEventPump
from .loop import DxLoopContext
from .offscreen import DxOffscreenBackend
from .particle_system import create_particle_overlay_factory
from .pet_window import create_pet_window_factory
from .scheduler import DxScheduler
from .screen import DxScreenProvider
from .screen_capture import DxScreenCapture
from .tray_host import create_tray_host_factory
from .window_host import create_dx_layer_window_host
from .world_object_backend import DxWorldObjectBackend


class DxDesktopBackend:
    """Own every service installed by one DirectX desktop bundle."""

    def __init__(
        self,
        *,
        warp: bool = False,
        context: DxLoopContext | None = None,
        screen_provider: DxScreenProvider | None = None,
        state_machine_factory=None,
        startup_sound_factory=None,
        interaction_sound_factory=None,
        particle_manager_provider=None,
        effect_manager_provider=None,
        workbench_opener=None,
    ) -> None:
        self.context = context or DxLoopContext()
        self.screen_provider = screen_provider or DxScreenProvider()
        self.screen_capture = DxScreenCapture(self.screen_provider)
        self.warp = bool(warp)
        self._state_machine_factory = state_machine_factory
        self._startup_sound_factory = startup_sound_factory
        self._interaction_sound_factory = interaction_sound_factory
        self._particle_manager_provider = particle_manager_provider
        self._effect_manager_provider = effect_manager_provider
        self._workbench_opener = workbench_opener
        self.world_object_backend = DxWorldObjectBackend(
            self.context,
            screen_provider=self.screen_provider,
            warp=self.warp,
        )
        self._schedulers: list[DxScheduler] = []
        self._event_pumps: list[DxEventPump] = []
        self._lock = threading.RLock()
        self._cleanup_done = False

    @property
    def cleaned(self) -> bool:
        with self._lock:
            return self._cleanup_done

    def _ensure_active(self) -> None:
        if self.cleaned:
            raise RuntimeError("DirectX desktop backend has been cleaned")

    def create_application_runtime(self) -> DxApplicationRuntime:
        self._ensure_active()
        return DxApplicationRuntime(self.context)

    def create_application_ui_host(self) -> DxApplicationUiHost:
        self._ensure_active()
        return DxApplicationUiHost(
            self.context,
            screen_provider=self.screen_provider,
            warp=self.warp,
            workbench_opener=self._workbench_opener,
        )

    def create_scheduler(self) -> DxScheduler:
        with self._lock:
            self._ensure_active()
            scheduler = DxScheduler(self.context)
            self._schedulers.append(scheduler)
            return scheduler

    def create_event_pump(self, callback: Callable[[], None]) -> DxEventPump:
        with self._lock:
            self._ensure_active()
            pump = DxEventPump(self.context, callback)
            self._event_pumps.append(pump)
            return pump

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self._ensure_active()
        self.context.call_later(delay_ms, callback)

    def create_screen_capture(self) -> DxScreenCapture:
        self._ensure_active()
        return DxScreenCapture(self.screen_provider)

    def bundle(self) -> DesktopBackendBundle:
        """Return the immutable service bundle bound to this owner."""
        self._ensure_active()
        return DesktopBackendBundle(
            draw_backend_factory=DxOffscreenBackend,
            application_runtime_factory=self.create_application_runtime,
            application_ui_host_factory=self.create_application_ui_host,
            scheduler_factory=self.create_scheduler,
            screen_capture_factory=self.create_screen_capture,
            pet_window_factory=create_pet_window_factory(
                self.context,
                screen_provider=self.screen_provider,
                state_machine_factory=self._state_machine_factory,
                startup_sound_factory=self._startup_sound_factory,
                interaction_sound_factory=self._interaction_sound_factory,
            ),
            particle_overlay_factory=create_particle_overlay_factory(
                self.context,
                screen_provider=self.screen_provider,
                warp=self.warp,
                particle_manager_provider=self._particle_manager_provider,
            ),
            effect_overlay_factory=create_effect_overlay_factory(
                self.context,
                screen_provider=self.screen_provider,
                warp=self.warp,
                effect_manager_provider=self._effect_manager_provider,
            ),
            tray_host_factory=create_tray_host_factory(
                self.context,
                warp=self.warp,
            ),
            event_pump_factory=self.create_event_pump,
            deferred_call=self.call_later,
            virtual_screen_provider=self.screen_provider.get_virtual_screen_rect,
            screen_for_point_provider=self.screen_provider.get_screen_rect_for_point,
            layer_window_host_factory=create_dx_layer_window_host,
            screen_capture_provider=self.screen_capture.capture_primary_png,
            window_host_factory=create_dx_layer_window_host,
            cleanup=self.cleanup,
        )

    def cleanup(self) -> None:
        """Release tracked services and any native hosts left by a failed exit."""
        with self._lock:
            if self._cleanup_done:
                return
            self._cleanup_done = True
            pumps, self._event_pumps = self._event_pumps, []
            schedulers, self._schedulers = self._schedulers, []

        first_error: BaseException | None = None
        try:
            self.world_object_backend.cleanup()
        except Exception as exc:
            first_error = exc

        for pump in pumps:
            try:
                pump.disconnect()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        for scheduler in schedulers:
            try:
                scheduler.cleanup()
            except Exception as exc:
                if first_error is None:
                    first_error = exc

        for poller in self.context.registered_pollers():
            try:
                cleanup = getattr(poller, "cleanup", None)
                if not callable(cleanup):
                    cleanup = getattr(poller, "close", None)
                if callable(cleanup):
                    cleanup()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            finally:
                self.context.unregister_poller(poller)

        if get_world_object_backend() is self.world_object_backend:
            reset_world_object_backend()
        if first_error is not None:
            raise first_error


_active_owner: DxDesktopBackend | None = None
_active_lock = threading.RLock()


def configure_dx_desktop_backend(
    *,
    warp: bool = False,
    state_machine_factory=None,
    startup_sound_factory=None,
    interaction_sound_factory=None,
    particle_manager_provider=None,
    effect_manager_provider=None,
    workbench_opener=None,
) -> None:
    """Install one complete Qt-free DirectX desktop composition."""
    global _active_owner
    with _active_lock:
        if _active_owner is not None and not _active_owner.cleaned:
            if _active_owner.warp != bool(warp):
                raise RuntimeError("DirectX desktop backend is already configured")
            bundle = _active_owner.bundle()
            configure_desktop_backend(**bundle.__dict__)
            configure_world_object_backend(_active_owner.world_object_backend)
            return

        owner = DxDesktopBackend(
            warp=warp,
            state_machine_factory=state_machine_factory,
            startup_sound_factory=startup_sound_factory,
            interaction_sound_factory=interaction_sound_factory,
            particle_manager_provider=particle_manager_provider,
            effect_manager_provider=effect_manager_provider,
            workbench_opener=workbench_opener,
        )
        try:
            bundle = owner.bundle()
            configure_desktop_backend(**bundle.__dict__)
            configure_world_object_backend(owner.world_object_backend)
        except Exception:
            owner.cleanup()
            raise
        _active_owner = owner


def get_dx_desktop_backend() -> DxDesktopBackend | None:
    return _active_owner


def cleanup_dx_desktop_backend() -> None:
    global _active_owner
    with _active_lock:
        owner, _active_owner = _active_owner, None
    if owner is not None:
        owner.cleanup()


__all__ = [
    "DxDesktopBackend",
    "cleanup_dx_desktop_backend",
    "configure_dx_desktop_backend",
    "get_dx_desktop_backend",
]
