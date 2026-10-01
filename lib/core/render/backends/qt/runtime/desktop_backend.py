"""Register the Qt desktop backend with backend-neutral core services."""

from lib.core.render.backends.base import DesktopBackendBundle
from lib.core.render.registry import install_desktop_backend_bundle
from lib.core.render.backends.qt.runtime.application_runtime import QtApplicationRuntime
from lib.core.render.backends.qt.drawing.draw_backend import QtDrawBackend
from lib.core.render.backends.qt.runtime.event_pump import create_event_pump
from lib.core.render.backends.qt.runtime.scheduler import call_later, create_scheduler
from lib.core.render.backends.qt.runtime.screen_capture import QtScreenCapture, capture_primary_screen_png
from lib.core.render.backends.qt.drawing.presentation import create_qt_presentation_host
from lib.core.render.backends.qt.runtime.providers import (
    create_qt_font_provider,
    create_qt_text_metrics,
)
from lib.core.render.backends.qt.runtime.screen import (
    get_screen_rect_for_point,
    get_virtual_screen_rect,
)
from lib.core.render.backends.qt.runtime.window_host import (
    create_qt_layer_window_host,
    create_qt_window_host,
)
from lib.core.world_objects import configure_world_object_backend


def configure_qt_desktop_backend(
    *,
    application_ui_host_factory,
    pet_window_factory,
    particle_overlay_factory,
    effect_overlay_factory,
    tray_host_factory,
    world_object_backend,
) -> None:
    """Select Qt implementations at the application composition boundary.

    The bundle is installed with this module-level function as its owner token,
    matching how the DirectX composition names itself. Selecting the Qt backend
    twice in one process is therefore a no-op instead of a second install, and
    selecting it while another backend is live is refused rather than silently
    stacked on top.
    """
    install_desktop_backend_bundle(
        DesktopBackendBundle(
            draw_backend_factory=QtDrawBackend,
            application_runtime_factory=QtApplicationRuntime,
            application_ui_host_factory=application_ui_host_factory,
            scheduler_factory=create_scheduler,
            screen_capture_factory=QtScreenCapture,
            pet_window_factory=pet_window_factory,
            particle_overlay_factory=particle_overlay_factory,
            effect_overlay_factory=effect_overlay_factory,
            tray_host_factory=tray_host_factory,
            event_pump_factory=create_event_pump,
            deferred_call=call_later,
            virtual_screen_provider=get_virtual_screen_rect,
            screen_for_point_provider=lambda point: get_screen_rect_for_point(point),
            layer_window_host_factory=create_qt_layer_window_host,
            screen_capture_provider=capture_primary_screen_png,
            window_host_factory=create_qt_window_host,
            presentation_host_factory=create_qt_presentation_host,
            font_provider_factory=create_qt_font_provider,
            text_metrics_factory=create_qt_text_metrics,
        ),
        owner=configure_qt_desktop_backend,
    )
    configure_world_object_backend(world_object_backend)
