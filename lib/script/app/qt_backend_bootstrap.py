"""Qt desktop backend composition entry."""
from __future__ import annotations


def _configure_qt_backend() -> None:
    from lib.core.qt_bridge.desktop_backend import configure_qt_desktop_backend
    from lib.script.cloudmusic._qt_player import QtMusicPlayer
    from lib.script.music.service import configure_music_player_factory

    configure_music_player_factory(QtMusicPlayer)
    configure_qt_desktop_backend()


def _configure_dx_backend() -> None:
    from lib.core.dx_bridge.desktop_backend import configure_dx_desktop_backend
    from lib.script.music.service import configure_music_player_factory

    configure_music_player_factory(None)
    configure_dx_desktop_backend()


def configure_selected_desktop_backend():
    """Register Qt lazily and apply the configured backend selection."""
    from config.config import UI
    from lib.core.backend_router import configure_selected_backend, register_backend

    register_backend("qt", _configure_qt_backend)
    register_backend("directx", _configure_dx_backend)
    return configure_selected_backend(UI.get("render_backend", "qt"))
