"""Register the Qt desktop backend with backend-neutral core services."""

from lib.core.desktop_backend import configure_desktop_backend
from lib.core.qt_bridge.application_runtime import QtApplicationRuntime
from lib.core.qt_bridge.application_ui import create_application_ui_host
from lib.core.qt_bridge.draw_backend import QtDrawBackend
from lib.core.qt_bridge.event_pump import create_event_pump
from lib.core.qt_bridge.effect_system import EffectOverlay
from lib.core.qt_bridge.particle_system import ParticleOverlay
from lib.core.qt_bridge.pet_window import QtPetWindow
from lib.core.qt_bridge.scheduler import call_later, create_scheduler
from lib.core.qt_bridge.screen_capture import QtScreenCapture, capture_primary_screen_png
from lib.core.qt_bridge.screen import (
    get_screen_rect_for_point,
    get_virtual_screen_rect,
)
from lib.core.qt_bridge.window_host import (
    create_qt_layer_window_host,
    create_qt_window_host,
)
from lib.core.qt_bridge.world_object_backend import QtWorldObjectBackend
from lib.core.qt_bridge.tray_host import get_tray_host
from lib.core.world_objects import configure_world_object_backend


def configure_qt_desktop_backend() -> None:
    """Select Qt implementations at the application composition boundary."""
    configure_desktop_backend(
        draw_backend_factory=QtDrawBackend,
        application_runtime_factory=QtApplicationRuntime,
        application_ui_host_factory=create_application_ui_host,
        scheduler_factory=create_scheduler,
        screen_capture_factory=QtScreenCapture,
        pet_window_factory=QtPetWindow,
        particle_overlay_factory=ParticleOverlay,
        effect_overlay_factory=EffectOverlay,
        tray_host_factory=get_tray_host,
        event_pump_factory=create_event_pump,
        deferred_call=call_later,
        virtual_screen_provider=get_virtual_screen_rect,
        screen_for_point_provider=lambda point: get_screen_rect_for_point(point),
        layer_window_host_factory=create_qt_layer_window_host,
        screen_capture_provider=capture_primary_screen_png,
        window_host_factory=create_qt_window_host,
    )
    configure_world_object_backend(QtWorldObjectBackend())
