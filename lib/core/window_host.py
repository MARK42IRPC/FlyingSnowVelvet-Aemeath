"""Backend-neutral window hosts used by core window ordering."""
from __future__ import annotations

import weakref
from collections.abc import Callable
from typing import Protocol


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
