"""Optional ctypes bridge for the DirectX offscreen prototype."""

from .offscreen import (
    DxBridgeError,
    DxOffscreenBackend,
    DxOffscreenTarget,
    find_dx_library,
)
from .application_runtime import DxApplication, DxApplicationRuntime
from .application_ui import DxApplicationUiHost, create_application_ui_host_factory
from .desktop_backend import (
    DxDesktopBackend,
    cleanup_dx_desktop_backend,
    configure_dx_desktop_backend,
    get_dx_desktop_backend,
)
from .event_pump import DxEventPump, create_event_pump
from .effect_system import DxEffectOverlay, create_effect_overlay_factory
from .loop import DxLoopContext, DxScheduledCall
from .overlay_window import DxOverlayWindow
from .particle_system import DxParticleOverlay, create_particle_overlay_factory
from .pet_window import DxPetWindow, create_pet_window_factory
from .screen import (
    DxMonitor,
    DxScreenProvider,
    get_cursor_position,
    get_screen_rect_for_point,
    get_virtual_screen_rect,
)
from .screen_capture import DxScreenCapture, capture_primary_screen_png
from .scheduler import DxPeriodicTimer, DxScheduler, create_scheduler
from .tray_host import DxTrayHost, create_tray_host_factory
from .window_host import DxHostEvent, DxWindowHost, create_dx_layer_window_host
from .world_object_backend import DxWorldObjectBackend

__all__ = [
    "DxApplication",
    "DxApplicationRuntime",
    "DxApplicationUiHost",
    "DxBridgeError",
    "DxEventPump",
    "DxEffectOverlay",
    "DxDesktopBackend",
    "DxLoopContext",
    "DxMonitor",
    "DxOffscreenBackend",
    "DxOffscreenTarget",
    "DxOverlayWindow",
    "DxPeriodicTimer",
    "DxParticleOverlay",
    "DxPetWindow",
    "DxScheduledCall",
    "DxScheduler",
    "DxScreenCapture",
    "DxScreenProvider",
    "DxTrayHost",
    "DxHostEvent",
    "DxWindowHost",
    "DxWorldObjectBackend",
    "capture_primary_screen_png",
    "cleanup_dx_desktop_backend",
    "configure_dx_desktop_backend",
    "create_application_ui_host_factory",
    "create_dx_layer_window_host",
    "create_event_pump",
    "create_effect_overlay_factory",
    "create_pet_window_factory",
    "create_particle_overlay_factory",
    "create_scheduler",
    "create_tray_host_factory",
    "find_dx_library",
    "get_cursor_position",
    "get_dx_desktop_backend",
    "get_screen_rect_for_point",
    "get_virtual_screen_rect",
]
