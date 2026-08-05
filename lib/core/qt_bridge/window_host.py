"""Qt and Win32 adapter for backend-neutral layer window ordering."""
from __future__ import annotations

import sys
import weakref
from collections.abc import Callable

from PyQt5 import sip

from lib.core.window_host import LayerWindowHost


SetWindowPosApi = Callable[[int, int, int, int, int, int, int], object]

_HWND_TOPMOST = -1
_SWP_FLAGS = 0x0213
_SET_WINDOW_POS_API: SetWindowPosApi | None = None
_SET_WINDOW_POS_API_LOADED = False


def _get_set_window_pos_api() -> SetWindowPosApi | None:
    global _SET_WINDOW_POS_API
    global _SET_WINDOW_POS_API_LOADED
    if _SET_WINDOW_POS_API_LOADED:
        return _SET_WINDOW_POS_API
    _SET_WINDOW_POS_API_LOADED = True
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    api = ctypes.windll.user32.SetWindowPos
    api.argtypes = (
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )
    api.restype = wintypes.BOOL
    _SET_WINDOW_POS_API = api
    return api


class QtLayerWindowHost:
    """Weak QWidget adapter with native z-order support on Windows."""

    def __init__(
        self,
        widget: object,
        *,
        set_window_pos_api: SetWindowPosApi | None = None,
    ) -> None:
        self._identity = id(widget)
        self._widget_ref = weakref.ref(widget)
        self._set_window_pos_api = (
            set_window_pos_api
            if set_window_pos_api is not None
            else _get_set_window_pos_api()
        )

    @property
    def identity(self) -> int:
        return self._identity

    def _widget(self) -> object | None:
        widget = self._widget_ref()
        if widget is None:
            return None
        try:
            if sip.isdeleted(widget):
                return None
        except TypeError:
            pass
        return widget

    def is_alive(self) -> bool:
        return self._widget() is not None

    def is_visible(self) -> bool:
        widget = self._widget()
        if widget is None:
            return False
        try:
            return bool(widget.isVisible())
        except RuntimeError:
            return False

    def raise_window(self) -> None:
        widget = self._widget()
        if widget is None:
            return
        try:
            widget.raise_()
        except RuntimeError:
            return

    def stack_window(self, insert_after: int | None) -> int | None:
        widget = self._widget()
        if widget is None or self._set_window_pos_api is None:
            return None
        try:
            hwnd = int(widget.winId())
            target = _HWND_TOPMOST if insert_after is None else int(insert_after)
            succeeded = self._set_window_pos_api(
                hwnd,
                target,
                0,
                0,
                0,
                0,
                _SWP_FLAGS,
            )
        except (RuntimeError, TypeError, ValueError):
            return None
        return hwnd if succeeded else None


def create_qt_layer_window_host(window: object) -> LayerWindowHost:
    return QtLayerWindowHost(window)
