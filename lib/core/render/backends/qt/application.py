"""Injection seam for the Qt application instance.

``qt_bridge.font`` and ``qt_bridge.screen`` need the live ``QApplication`` but
must also stay drivable without one, so every lookup goes through this single
getter instead of calling ``QApplication.instance()`` at each site. Tests
install a stand-in application object and restore the default afterwards,
the same way :class:`lib.core.render.backends.qt.screen_capture.QtScreenCapture` takes
an ``application_getter``.

This module lives in ``lib/core/render/backends/qt`` and may import PyQt5.
"""
from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtWidgets import QApplication

#: Returns the live application, or ``None`` before one has been created.
ApplicationGetter = Callable[[], object | None]

_default_getter: ApplicationGetter = QApplication.instance
_application_getter: ApplicationGetter = _default_getter


def get_application() -> object | None:
    """Return the active application object, or None when there is none."""
    return _application_getter()


def set_application_getter(getter: ApplicationGetter | None) -> None:
    """Install the active getter; ``None`` restores ``QApplication.instance``."""
    global _application_getter
    if getter is not None and not callable(getter):
        raise TypeError("application getter must be callable or None")
    _application_getter = getter or _default_getter


__all__ = [
    "ApplicationGetter",
    "get_application",
    "set_application_getter",
]
