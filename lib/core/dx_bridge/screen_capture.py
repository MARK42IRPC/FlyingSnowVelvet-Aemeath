"""Qt-free Win32 primary-screen capture with PNG output."""
from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from io import BytesIO

from lib.core.graphics.types import Rect
from lib.core.logger import get_logger

from .screen import DxScreenProvider


logger = get_logger(__name__)

BI_RGB = 0
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020
_HGDI_ERROR = ctypes.c_void_p(-1).value


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BitmapInfoHeader),
        ("bmiColors", wintypes.DWORD * 1),
    ]


def _capture_win32_bgra(rect: Rect) -> bytes | None:
    if not hasattr(ctypes, "WinDLL"):
        return None
    width = max(1, int(round(rect.width)))
    height = max(1, int(round(rect.height)))
    x = int(round(rect.x))
    y = int(round(rect.y))

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.BitBlt.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(_BitmapInfo),
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = wintypes.BOOL

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        return None
    memory_dc = None
    bitmap = None
    previous_object = None
    try:
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            return None
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not bitmap:
            return None
        previous_object = gdi32.SelectObject(memory_dc, bitmap)
        if not previous_object or previous_object == _HGDI_ERROR:
            return None
        if not gdi32.BitBlt(
            memory_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            x,
            y,
            SRCCOPY | CAPTUREBLT,
        ):
            return None

        expected_size = width * height * 4
        pixels = (ctypes.c_ubyte * expected_size)()
        bitmap_info = _BitmapInfo()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = BI_RGB
        restored_object = gdi32.SelectObject(memory_dc, previous_object)
        if not restored_object or restored_object == _HGDI_ERROR:
            return None
        previous_object = None
        if gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            pixels,
            ctypes.byref(bitmap_info),
            DIB_RGB_COLORS,
        ) != height:
            return None
        return bytes(pixels)
    finally:
        if previous_object and memory_dc:
            gdi32.SelectObject(memory_dc, previous_object)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


class DxScreenCapture:
    """Capture primary-screen pixels without exposing Win32 or Pillow objects."""

    def __init__(
        self,
        screen_provider: DxScreenProvider | None = None,
        pixel_capture: Callable[[Rect], bytes | None] | None = None,
    ) -> None:
        self._screen_provider = screen_provider or DxScreenProvider()
        self._pixel_capture = pixel_capture or _capture_win32_bgra

    def capture_primary_png(self) -> bytes | None:
        try:
            rect = self._screen_provider.get_primary_screen_rect()
            width = max(1, int(round(rect.width)))
            height = max(1, int(round(rect.height)))
            pixels = self._pixel_capture(rect)
            if pixels is None or len(pixels) != width * height * 4:
                logger.warning("[Vision] DX 主屏幕截图像素为空或尺寸无效")
                return None

            from PIL import Image

            image = Image.frombytes("RGB", (width, height), pixels, "raw", "BGRX")
            output = BytesIO()
            image.save(output, format="PNG")
            payload = output.getvalue()
            if not payload:
                logger.warning("[Vision] DX 主屏幕截图编码为空")
                return None
            return payload
        except Exception as exc:
            logger.error("[Vision] DX 主屏幕截图失败: %s", exc)
            return None


_default_capture = DxScreenCapture()


def capture_primary_screen_png() -> bytes | None:
    return _default_capture.capture_primary_png()


__all__ = ["DxScreenCapture", "capture_primary_screen_png"]
