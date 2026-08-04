"""Register the Qt desktop backend with backend-neutral core services."""

from lib.core.desktop_backend import configure_desktop_backend
from lib.core.qt_bridge.draw_backend import QtDrawBackend
from lib.core.qt_bridge.event_pump import create_event_pump
from lib.core.qt_bridge.scheduler import call_later
from lib.core.qt_bridge.screen_capture import capture_primary_screen_png
from lib.core.qt_bridge.screen import (
    get_screen_rect_for_point,
    get_virtual_screen_rect,
)
from lib.core.qt_bridge.world_object_backend import QtWorldObjectBackend
from lib.core.world_objects import configure_world_object_backend


def configure_qt_desktop_backend() -> None:
    """Select Qt implementations at the application composition boundary."""
    configure_desktop_backend(
        draw_backend_factory=QtDrawBackend,
        event_pump_factory=create_event_pump,
        deferred_call=call_later,
        virtual_screen_provider=get_virtual_screen_rect,
        screen_for_point_provider=lambda point: get_screen_rect_for_point(point),
        screen_capture_provider=capture_primary_screen_png,
    )
    configure_world_object_backend(QtWorldObjectBackend())
