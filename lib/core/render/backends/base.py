"""后端组合契约：一个后端安装进进程时提供的全部工厂。

这里只描述“一个后端要提供什么”，不含任何具体实现，也不含注册表状态。
注册与安装语义在 `lib/core/render/registry.py`；装配在 `lib/core/render/router.py`。
两个后端（Qt / DX）各自把自己的实现填进同一个 `DesktopBackendBundle`。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from lib.core.application_runtime import ApplicationRuntime
from lib.core.application_ui import ApplicationUiHostFactory
from lib.core.event.pump import EventPumpFactory
from lib.core.overlay_host import OverlayHost
from lib.core.pet_host import PetWindowHost
from lib.core.render.visuals.backend import DrawBackend
from lib.core.render.visuals.rich_text_parser import TextSegment
from lib.core.render.visuals.types import FontSpec
from lib.core.render.visuals.capture import ScreenCapture
from lib.core.render.visuals.types import Point, Rect
from lib.core.timing.scheduler import Scheduler
from lib.core.tray_host import TrayHostFactory
from lib.core.window_host import LayerWindowHostFactory, WindowHostFactory


class PresentationHost(Protocol):
    """后端中立的窗口呈现几何：屏幕归属、夹取与坐标换算。

    这些能力必须留在后端里（只有 Qt 知道 `windowHandle()`、只有 Win32 知道
    `MonitorFromPoint`），但业务层需要的是核心 `Rect` / `Point`，不是 `QRect`。
    实现这一协议的后端把"窗口属于哪块屏"翻译成核心几何，调用方因此不必 import
    任何具体后端路径。取不到屏幕信息时返回 `None`，由调用方自行回退。
    """

    def screen_rect_for_widget(self, widget, point=None) -> Rect | None:
        """返回该控件当前所在的屏幕矩形。"""

    def clamp_position(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        widget=None,
        point=None,
    ) -> tuple[int, int] | None:
        """把窗口左上角夹取到该控件所在屏幕内，返回核心坐标对。"""

    def widget_global_rect(self, widget) -> Rect:
        """返回控件在屏幕坐标系中的矩形（核心类型）。"""

    def move_widget_to_global(self, widget, x: int, y: int) -> None:
        """按屏幕坐标移动控件，宿主分层时换算成宿主本地坐标。"""

    def widget_global_point(self, widget, point) -> Point:
        """把控件本地坐标点换算成屏幕坐标点。"""


class FontProvider(Protocol):
    """后端中立的字体取用：UI 字体、数字字体、命令字体与族名。

    控件拿到的是后端自己的字体对象（Qt 下就是 `QFont`）——控件本身是工具包的
    控件，这一点不变。收敛的是"从哪里取"，不是"取到的是什么类型"。
    """

    def ui_font(self, size: int | None = None) -> object:
        """UI 正文/标签字体。"""

    def digit_font(self, size: int | None = None) -> object:
        """数字与拉丁字形字体。"""

    def cmd_font(self, size: int | None = None) -> object:
        """命令框等宽/终端风格字体。"""

    def ui_font_family(self) -> str:
        """已注册的 UI 字体族名。"""

    def apply_ui_font_tree(self, widget) -> None:
        """把 UI 字体族刷到整棵控件树上。"""


class TextMetrics(Protocol):
    """后端中立的字形度量：presenter 换行、省略号与基线全由共享逻辑决定。"""

    default_font: FontSpec
    digit_font: FontSpec
    side_font: FontSpec
    default_line_height: float
    digit_line_height: float
    default_ascent: float
    default_descent: float
    digit_ascent: float
    digit_descent: float

    def measure(self, text: str, *, digit: bool = False, side: bool = False) -> float:
        """返回该字体的水平推进量。"""

    def measure_segment(self, segment: TextSegment) -> float:
        """返回富文本分段的推进量。"""

    def ascent_for(self, text: str, *, digit: bool = False, side: bool = False) -> float:
        """返回绘制该文本时后端实际使用的基线高度。"""


DrawBackendFactory = Callable[[], DrawBackend]
PresentationHostFactory = Callable[[], PresentationHost]
FontProviderFactory = Callable[[], FontProvider]
TextMetricsFactory = Callable[..., TextMetrics]
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
    #: 呈现几何、字体与文本度量由后端注入。可选：DX 尚未接线时留空，调用方
    #: 走后端中立回退而不是崩溃——这两条能力是"何时接"的排期问题，不是契约缺口。
    presentation_host_factory: PresentationHostFactory | None = None
    font_provider_factory: FontProviderFactory | None = None
    text_metrics_factory: TextMetricsFactory | None = None
