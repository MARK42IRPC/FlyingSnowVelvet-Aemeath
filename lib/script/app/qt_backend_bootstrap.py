"""Qt desktop backend composition entry."""
from __future__ import annotations


def _configure_qt_backend() -> None:
    from lib.core.qt_bridge.desktop_backend import configure_qt_desktop_backend

    configure_qt_desktop_backend()


def configure_selected_desktop_backend():
    """Register Qt lazily and apply the configured backend selection."""
    from config.config import UI
    from lib.core.backend_router import configure_selected_backend, register_backend

    register_backend("qt", _configure_qt_backend)
    return configure_selected_backend(UI.get("render_backend", "qt"))
