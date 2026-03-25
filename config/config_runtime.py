"""???????????????"""

from __future__ import annotations

import ctypes

from config.scale import set_draw_scale, get_user_scale
from config.user_scale_config import get_user_scale_config

_SCALE_BASELINE_HEIGHT = 1080.0


class _DEVMODEW(ctypes.Structure):
    """Win32 DEVMODEW 结构，用于读取主屏幕原生分辨率。"""

    _fields_ = [
        ('dmDeviceName', ctypes.c_wchar * 32),
        ('dmSpecVersion', ctypes.c_ushort),
        ('dmDriverVersion', ctypes.c_ushort),
        ('dmSize', ctypes.c_ushort),
        ('dmDriverExtra', ctypes.c_ushort),
        ('dmFields', ctypes.c_ulong),
        ('dmOrientation', ctypes.c_short),
        ('dmPaperSize', ctypes.c_short),
        ('dmPaperLength', ctypes.c_short),
        ('dmPaperWidth', ctypes.c_short),
        ('dmScale', ctypes.c_short),
        ('dmCopies', ctypes.c_short),
        ('dmDefaultSource', ctypes.c_short),
        ('dmPrintQuality', ctypes.c_short),
        ('dmColor', ctypes.c_short),
        ('dmDuplex', ctypes.c_short),
        ('dmYResolution', ctypes.c_short),
        ('dmTTOption', ctypes.c_short),
        ('dmCollate', ctypes.c_short),
        ('dmFormName', ctypes.c_wchar * 32),
        ('dmLogPixels', ctypes.c_ushort),
        ('dmBitsPerPel', ctypes.c_ulong),
        ('dmPelsWidth', ctypes.c_ulong),
        ('dmPelsHeight', ctypes.c_ulong),
        ('dmDisplayFlags', ctypes.c_ulong),
        ('dmDisplayFrequency', ctypes.c_ulong),
        ('dmICMMethod', ctypes.c_ulong),
        ('dmICMIntent', ctypes.c_ulong),
        ('dmMediaType', ctypes.c_ulong),
        ('dmDitherType', ctypes.c_ulong),
        ('dmReserved1', ctypes.c_ulong),
        ('dmReserved2', ctypes.c_ulong),
        ('dmPanningWidth', ctypes.c_ulong),
        ('dmPanningHeight', ctypes.c_ulong),
    ]

def _get_primary_screen_resolution() -> tuple[int, int]:
    """获取主屏分辨率；失败时回退到 1920x1080。"""
    try:
        user32 = ctypes.windll.user32
        dev_mode = _DEVMODEW()
        dev_mode.dmSize = ctypes.sizeof(_DEVMODEW)
        enum_current_settings = -1
        if user32.EnumDisplaySettingsW(None, enum_current_settings, ctypes.byref(dev_mode)):
            width = int(dev_mode.dmPelsWidth)
            height = int(dev_mode.dmPelsHeight)
            if width > 0 and height > 0:
                return width, height
    except Exception:
        pass

    try:
        user32 = ctypes.windll.user32
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass

    return 1920, 1080

def _resolve_draw_scale(scale_value, screen_height: int) -> float:
    """支持手动值和 auto；auto 使用屏幕高度 / 1080。"""
    if isinstance(scale_value, str) and scale_value.strip().lower() == 'auto':
        if screen_height <= 0:
            return 1.0
        return round(screen_height / _SCALE_BASELINE_HEIGHT, 3)
    return scale_value

_screen_width, _screen_height = _get_primary_screen_resolution()

DRAW = {
    # 默认启用线性自适应：1080p=1.0, 2160p=2.0（screen_height/1080）
    'scale': 1.0,  # 支持: auto / 1.5 / "1.5" / "1,5" / "150%"
    'screen_width': _screen_width,
    'screen_height': _screen_height,
    'scale_rule': 'screen_height/1080',
}
DRAW['scale'] = _resolve_draw_scale(DRAW.get('scale', 'auto'), _screen_height)
set_draw_scale(DRAW['scale'])

STARTUP = {
    'ensure_desktop_shortcut': True,
}

_user_scale_config = get_user_scale_config()
USER_SCALE = get_user_scale()
