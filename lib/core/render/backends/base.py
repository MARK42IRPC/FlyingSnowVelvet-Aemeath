"""后端组合契约：一个后端安装进进程时提供的全部工厂。

这里只描述“一个后端要提供什么”，不含任何具体实现，也不含注册表状态。
注册与安装语义在 `lib/core/render/registry.py`；装配在 `lib/core/render/router.py`。
两个后端（Qt / DX）各自把自己的实现填进同一个 `DesktopBackendBundle`。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lib.core.application_runtime import ApplicationRuntime
from lib.core.application_ui import ApplicationUiHostFactory
from lib.core.event.pump import EventPumpFactory
from lib.core.overlay_host import OverlayHost
from lib.core.pet_host import PetWindowHost
from lib.core.render.visuals.backend import DrawBackend
from lib.core.render.visuals.capture import ScreenCapture
from lib.core.render.visuals.types import Point, Rect
from lib.core.timing.scheduler import Scheduler
from lib.core.tray_host import TrayHostFactory
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
