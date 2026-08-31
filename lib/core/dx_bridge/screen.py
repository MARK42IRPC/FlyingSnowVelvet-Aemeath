"""Qt-free Win32 display topology queries for the DirectX backend."""
from __future__ import annotations

import ctypes
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from ctypes import wintypes

from lib.core.graphics.screen import screen_for_point, virtual_screen_rect
from lib.core.graphics.types import Point, Rect


MONITORINFOF_PRIMARY = 0x00000001
_DEFAULT_SCREEN = Rect(0, 0, 1920, 1080)


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


_MonitorEnumProc = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    wintypes.BOOL,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)


@dataclass(frozen=True, slots=True)
class DxMonitor:
    """One current Win32 monitor snapshot."""

    geometry: Rect
    work_area: Rect
    primary: bool = False
    dpi: int = 96


def _rect_from_win32(rect: wintypes.RECT) -> Rect:
    return Rect(
        int(rect.left),
        int(rect.top),
        max(1, int(rect.right - rect.left)),
        max(1, int(rect.bottom - rect.top)),
    )


def _enumerate_win32_monitors() -> tuple[DxMonitor, ...]:
    if not hasattr(ctypes, "WinDLL"):
        return ()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumDisplayMonitors.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.RECT),
        _MonitorEnumProc,
        wintypes.LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_MonitorInfo),
    ]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    get_monitor_dpi = None
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        get_monitor_dpi = shcore.GetDpiForMonitor
        get_monitor_dpi.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        ]
        get_monitor_dpi.restype = ctypes.c_long
    except (AttributeError, OSError):
        get_monitor_dpi = None

    monitors: list[DxMonitor] = []

    @_MonitorEnumProc
    def append_monitor(monitor, _device_context, _monitor_rect, _data):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            dpi_x = wintypes.UINT(96)
            dpi_y = wintypes.UINT(96)
            if get_monitor_dpi is not None:
                if get_monitor_dpi(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) != 0:
                    dpi_x.value = 96
            monitors.append(
                DxMonitor(
                    geometry=_rect_from_win32(info.rcMonitor),
                    work_area=_rect_from_win32(info.rcWork),
                    primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                    dpi=max(1, int(dpi_x.value)),
                )
            )
        return True

    if not user32.EnumDisplayMonitors(None, None, append_monitor, 0):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, "EnumDisplayMonitors failed")
    return tuple(monitors)


class DxScreenProvider:
    """Read the current monitor topology without retaining stale snapshots."""

    def __init__(
        self,
        monitor_loader: Callable[[], Iterable[DxMonitor]] | None = None,
        *,
        fallback: Rect = _DEFAULT_SCREEN,
    ) -> None:
        self._monitor_loader = monitor_loader or _enumerate_win32_monitors
        self._fallback = fallback

    def monitors(self) -> tuple[DxMonitor, ...]:
        try:
            return tuple(self._monitor_loader())
        except OSError:
            return ()

    def get_primary_screen_rect(self) -> Rect:
        monitors = self.monitors()
        for monitor in monitors:
            if monitor.primary:
                return monitor.geometry
        return monitors[0].geometry if monitors else self._fallback

    def get_virtual_screen_rect(self) -> Rect:
        return virtual_screen_rect(
            (monitor.geometry for monitor in self.monitors()),
            fallback=self._fallback,
        )

    def get_screen_rect_for_point(self, point: Point | None = None) -> Rect:
        monitors = self.monitors()
        fallback = next(
            (monitor.geometry for monitor in monitors if monitor.primary),
            monitors[0].geometry if monitors else self._fallback,
        )
        return screen_for_point(
            point,
            (monitor.geometry for monitor in monitors),
            fallback,
        )

    def get_dpi_for_point(self, point: Point | None = None) -> int:
        monitors = self.monitors()
        if not monitors:
            return 96
        geometry = screen_for_point(
            point,
            (monitor.geometry for monitor in monitors),
            next(
                (monitor.geometry for monitor in monitors if monitor.primary),
                monitors[0].geometry,
            ),
        )
        monitor = next((item for item in monitors if item.geometry == geometry), monitors[0])
        return max(1, int(monitor.dpi))

    def get_scale_for_point(self, point: Point | None = None) -> float:
        return self.get_dpi_for_point(point) / 96.0


def get_cursor_position() -> Point:
    """Return the current Win32 cursor position in desktop coordinates."""
    if not hasattr(ctypes, "WinDLL"):
        return Point()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    position = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(position)):
        return Point()
    return Point(int(position.x), int(position.y))


_default_provider = DxScreenProvider()


def get_virtual_screen_rect() -> Rect:
    return _default_provider.get_virtual_screen_rect()


def get_screen_rect_for_point(point: Point | None = None) -> Rect:
    return _default_provider.get_screen_rect_for_point(point)


__all__ = [
    "DxMonitor",
    "DxScreenProvider",
    "get_cursor_position",
    "get_screen_rect_for_point",
    "get_virtual_screen_rect",
]
