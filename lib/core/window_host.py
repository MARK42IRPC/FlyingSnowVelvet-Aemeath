"""Backend-neutral window hosts used by core window ordering."""
from __future__ import annotations

import weakref
from collections.abc import Callable
from typing import Protocol

from lib.core.graphics.types import Rect


class LayerWindowHost(Protocol):
    """Minimal window surface required by ``LayerManager``."""

    @property
    def identity(self) -> int:
        """Return a stable identity for registration and removal."""
        ...

    def is_alive(self) -> bool:
        """Return whether the backend window can still be used."""
        ...

    def is_visible(self) -> bool:
        """Return whether the window participates in z-order updates."""
        ...

    def raise_window(self) -> None:
        """Raise the window when explicit native stacking is unavailable."""
        ...

    def stack_window(self, insert_after: int | None) -> int | None:
        """Stack the window and return its backend-native stacking token."""
        ...


LayerWindowHostFactory = Callable[[object], LayerWindowHost]


class WindowHost(LayerWindowHost, Protocol):
    """Backend-neutral lifecycle and input surface for one desktop window."""

    @property
    def native_handle(self) -> int | None:
        """Return the native window handle when one exists."""
        ...

    def show(self) -> None:
        """Show the window without changing its activation policy."""
        ...

    def hide(self) -> None:
        """Hide the window without destroying it."""
        ...

    def close(self) -> None:
        """Close the native window."""
        ...

    def get_geometry(self) -> Rect:
        """Return the window frame in physical desktop coordinates."""
        ...

    def set_geometry(self, geometry: Rect) -> None:
        """Set the window frame in physical desktop coordinates."""
        ...

    def get_dpi(self) -> int:
        """Return the effective logical DPI for the window."""
        ...

    def get_screen_geometry(self) -> Rect | None:
        """Return the containing screen geometry when available."""
        ...

    def set_clickthrough(self, enabled: bool) -> None:
        """Enable or disable pointer hit-test pass-through."""
        ...

    def is_clickthrough_enabled(self) -> bool:
        """Return the requested pointer pass-through state."""
        ...

    def is_active(self) -> bool:
        """Return whether the window currently owns activation."""
        ...

    def activate(self) -> None:
        """Request activation according to the backend's focus policy."""
        ...

    def capture_mouse(self) -> None:
        """Capture pointer input for drag operations."""
        ...

    def release_mouse(self) -> None:
        """Release a pointer capture requested by this host."""
        ...

    def has_mouse_capture(self) -> bool:
        """Return whether this host currently owns pointer capture."""
        ...

    def request_repaint(self, viewport: Rect | None = None) -> None:
        """Request one repaint, optionally limited to a viewport."""
        ...

    def cleanup(self) -> None:
        """Release capture and close the host idempotently."""
        ...


WindowHostFactory = Callable[[object], WindowHost]


class PassiveLayerWindowHost:
    """No-window fallback used before a desktop backend is configured."""

    def __init__(self, window: object) -> None:
        self._identity = id(window)
        self._strong_window: object | None = None
        try:
            self._window_ref: weakref.ReferenceType[object] | None = weakref.ref(window)
        except TypeError:
            self._window_ref = None
            self._strong_window = window

    @property
    def identity(self) -> int:
        return self._identity

    def is_alive(self) -> bool:
        return self._window_ref is None or self._window_ref() is not None

    def is_visible(self) -> bool:
        return self.is_alive()

    def raise_window(self) -> None:
        return None

    def stack_window(self, insert_after: int | None) -> int | None:
        return None


def create_passive_layer_window_host(window: object) -> LayerWindowHost:
    return PassiveLayerWindowHost(window)


class PassiveWindowHost(PassiveLayerWindowHost):
    """Stateful no-native host used by core tests and unconfigured startup."""

    def __init__(self, window: object) -> None:
        super().__init__(window)
        self._visible = False
        self._closed = False
        self._geometry = Rect()
        self._dpi = 96
        self._screen_geometry: Rect | None = None
        self._clickthrough = False
        self._active = False
        self._mouse_capture = False
        self._repaint_requests: list[Rect | None] = []

    @property
    def native_handle(self) -> int | None:
        return None

    def is_alive(self) -> bool:
        return not self._closed and super().is_alive()

    def is_visible(self) -> bool:
        return self.is_alive() and self._visible

    def show(self) -> None:
        if self.is_alive():
            self._visible = True

    def hide(self) -> None:
        if self.is_alive():
            self._visible = False
            self._active = False

    def close(self) -> None:
        if self._closed:
            return
        self.release_mouse()
        self._visible = False
        self._active = False
        self._closed = True

    def get_geometry(self) -> Rect:
        return self._geometry

    def set_geometry(self, geometry: Rect) -> None:
        if not isinstance(geometry, Rect):
            raise TypeError("geometry must be a Rect")
        if self.is_alive():
            self._geometry = geometry

    def get_dpi(self) -> int:
        return self._dpi

    def get_screen_geometry(self) -> Rect | None:
        return self._screen_geometry

    def set_clickthrough(self, enabled: bool) -> None:
        if self.is_alive():
            self._clickthrough = bool(enabled)

    def is_clickthrough_enabled(self) -> bool:
        return self._clickthrough

    def is_active(self) -> bool:
        return self.is_alive() and self._active

    def activate(self) -> None:
        if self.is_visible() and not self._clickthrough:
            self._active = True

    def capture_mouse(self) -> None:
        if self.is_alive():
            self._mouse_capture = True

    def release_mouse(self) -> None:
        self._mouse_capture = False

    def has_mouse_capture(self) -> bool:
        return self.is_alive() and self._mouse_capture

    def request_repaint(self, viewport: Rect | None = None) -> None:
        if viewport is not None and not isinstance(viewport, Rect):
            raise TypeError("viewport must be a Rect or None")
        if self.is_alive():
            self._repaint_requests.append(viewport)

    def cleanup(self) -> None:
        self.close()


def create_passive_window_host(window: object) -> WindowHost:
    return PassiveWindowHost(window)


__all__ = [
    "LayerWindowHost",
    "LayerWindowHostFactory",
    "PassiveLayerWindowHost",
    "PassiveWindowHost",
    "WindowHost",
    "WindowHostFactory",
    "create_passive_layer_window_host",
    "create_passive_window_host",
]
