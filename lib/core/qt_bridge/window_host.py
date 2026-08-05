"""Qt and Win32 adapter for backend-neutral layer window ordering."""
from __future__ import annotations

import sys
import weakref
from collections.abc import Callable

from PyQt5 import sip
from PyQt5.QtCore import QRect, Qt

from lib.core.graphics.types import Rect
from lib.core.window_host import LayerWindowHost, WindowHost


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


class QtWindowHost(QtLayerWindowHost):
    """Qt QWidget adapter for the backend-neutral WindowHost v1 surface."""

    def __init__(self, widget: object, *, set_window_pos_api: SetWindowPosApi | None = None) -> None:
        super().__init__(widget, set_window_pos_api=set_window_pos_api)
        self._clickthrough = False
        self._mouse_capture = False
        self._cleaned = False

    def _get_native_handle(self) -> int | None:
        widget = self._widget()
        if widget is None:
            return None
        try:
            return int(widget.winId())
        except (RuntimeError, TypeError, ValueError):
            return None

    @property
    def native_handle(self) -> int | None:
        return self._get_native_handle()

    def show(self) -> None:
        widget = self._widget()
        if widget is None:
            return
        try:
            widget.show()
        except RuntimeError:
            return

    def hide(self) -> None:
        widget = self._widget()
        if widget is None:
            return
        try:
            widget.hide()
        except RuntimeError:
            return

    def close(self) -> None:
        widget = self._widget()
        if widget is None:
            return
        try:
            widget.close()
        except RuntimeError:
            return

    @staticmethod
    def _rect_from_qt(rectangle: object) -> Rect:
        return Rect(
            float(rectangle.x()),
            float(rectangle.y()),
            float(rectangle.width()),
            float(rectangle.height()),
        )

    def get_geometry(self) -> Rect:
        widget = self._widget()
        if widget is None:
            return Rect()
        try:
            geometry_getter = getattr(widget, "frameGeometry", None)
            rectangle = geometry_getter() if callable(geometry_getter) else widget.geometry()
            return self._rect_from_qt(rectangle)
        except (RuntimeError, AttributeError, TypeError):
            return Rect()

    def set_geometry(self, geometry: Rect) -> None:
        if not isinstance(geometry, Rect):
            raise TypeError("geometry must be a Rect")
        widget = self._widget()
        if widget is None:
            return
        try:
            widget.setGeometry(
                int(round(geometry.x)),
                int(round(geometry.y)),
                max(1, int(round(geometry.width))),
                max(1, int(round(geometry.height))),
            )
        except (RuntimeError, AttributeError, TypeError, ValueError):
            return

    def get_dpi(self) -> int:
        widget = self._widget()
        if widget is None:
            return 96
        try:
            screen = widget.screen()
            dpi_getter = getattr(screen, "logicalDotsPerInchX", None)
            if callable(dpi_getter):
                return max(1, int(round(float(dpi_getter()))))
        except (RuntimeError, AttributeError, TypeError, ValueError):
            pass
        return 96

    def get_screen_geometry(self) -> Rect | None:
        widget = self._widget()
        if widget is None:
            return None
        try:
            screen = widget.screen()
            if screen is None:
                return None
            return self._rect_from_qt(screen.geometry())
        except (RuntimeError, AttributeError, TypeError, ValueError):
            return None

    def set_clickthrough(self, enabled: bool) -> None:
        widget = self._widget()
        if widget is None:
            return
        try:
            widget.setAttribute(Qt.WA_TransparentForMouseEvents, bool(enabled))
            self._clickthrough = bool(enabled)
        except (RuntimeError, AttributeError, TypeError):
            return

    def is_clickthrough_enabled(self) -> bool:
        return self._clickthrough

    def is_active(self) -> bool:
        widget = self._widget()
        if widget is None:
            return False
        try:
            return bool(widget.isActiveWindow())
        except (RuntimeError, AttributeError):
            return False

    def activate(self) -> None:
        widget = self._widget()
        if widget is None or self._clickthrough:
            return
        try:
            widget.activateWindow()
        except (RuntimeError, AttributeError):
            return

    def capture_mouse(self) -> None:
        widget = self._widget()
        if widget is None:
            return
        try:
            widget.grabMouse()
            self._mouse_capture = True
        except (RuntimeError, AttributeError):
            return

    def release_mouse(self) -> None:
        widget = self._widget()
        if widget is not None:
            try:
                widget.releaseMouse()
            except (RuntimeError, AttributeError):
                pass
        self._mouse_capture = False

    def has_mouse_capture(self) -> bool:
        return self.is_alive() and self._mouse_capture

    def request_repaint(self, viewport: Rect | None = None) -> None:
        widget = self._widget()
        if widget is None:
            return
        if viewport is not None and not isinstance(viewport, Rect):
            raise TypeError("viewport must be a Rect or None")
        try:
            if viewport is None:
                widget.update()
            else:
                widget.update(QRect(
                    int(round(viewport.x)),
                    int(round(viewport.y)),
                    max(1, int(round(viewport.width))),
                    max(1, int(round(viewport.height))),
                ))
        except (RuntimeError, AttributeError, TypeError, ValueError):
            return

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self.release_mouse()
        self.close()


def create_qt_window_host(window: object) -> WindowHost:
    return QtWindowHost(window)


__all__ = [
    "QtLayerWindowHost",
    "QtWindowHost",
    "create_qt_layer_window_host",
    "create_qt_window_host",
]
