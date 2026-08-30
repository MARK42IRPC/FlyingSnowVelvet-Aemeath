"""Qt desktop backend composition entry."""
from __future__ import annotations


_QT_WORLD_OBJECT_TYPES = {
    "motor": ("lib.script.ui.world_objects.motor", "Mortor"),
    "clock": ("lib.script.ui.world_objects.clock", "Clock"),
    "sofa": ("lib.script.ui.world_objects.sofa", "Sofa"),
    "snow_pile": ("lib.script.ui.world_objects.snow_pile", "SnowPile"),
    "snowball": ("lib.script.ui.world_objects.snowball", "Snowball"),
    "snow_leopard": ("lib.script.ui.world_objects.snow_leopard", "SnowLeopard"),
    "speaker": ("lib.script.ui.world_objects.speaker", "Speaker"),
}


def _product_runtime_factories():
    from lib.script.effects.manager import get_effect_script_manager
    from lib.script.mainpet.state import StateMachine
    from lib.script.practical.manager import get_particle_script_manager
    from lib.script.voice.ams_startup import AmsStartupSound
    from lib.script.voice.ams_enh import AmsEnhSound

    return {
        "state_machine_factory": StateMachine,
        "startup_sound_factory": AmsStartupSound,
        "interaction_sound_factory": AmsEnhSound,
        "particle_manager_provider": get_particle_script_manager,
        "effect_manager_provider": get_effect_script_manager,
    }


def _open_workbench(initial_page: str) -> bool:
    from lib.script.app.workbench_helper import launch_workbench_helper

    return bool(launch_workbench_helper(initial_page=initial_page))


def _configure_qt_backend() -> None:
    from lib.core.qt_bridge.desktop_backend import configure_qt_desktop_backend
    from lib.core.qt_bridge.effect_system import create_effect_overlay_factory
    from lib.core.qt_bridge.particle_system import create_particle_overlay_factory
    from lib.core.qt_bridge.pet_window import create_qt_pet_window_factory
    from lib.core.qt_bridge.tray_host import create_tray_host_factory
    from lib.core.qt_bridge.world_object_backend import QtWorldObjectBackend
    from lib.script.cloudmusic._qt_player import QtMusicPlayer
    from lib.script.effects.manager import cleanup_effect_script_manager
    from lib.script.music.service import configure_music_player_factory
    from lib.script.app.qt_application_ui import create_application_ui_host
    from lib.script.ui.pet_window_ui import (
        attach_pet_window_ui,
        preload_pet_window_ui,
        shutdown_pet_window_ui,
    )
    from lib.script.ui.tray_icon import cleanup_tray_icon, get_tray_icon

    configure_music_player_factory(QtMusicPlayer)
    product = _product_runtime_factories()
    configure_qt_desktop_backend(
        application_ui_host_factory=create_application_ui_host,
        pet_window_factory=create_qt_pet_window_factory(
            state_machine_factory=product["state_machine_factory"],
            startup_sound_factory=product["startup_sound_factory"],
            interaction_sound_factory=product["interaction_sound_factory"],
            attach_ui=attach_pet_window_ui,
            preload_ui=preload_pet_window_ui,
            shutdown_ui=shutdown_pet_window_ui,
        ),
        particle_overlay_factory=create_particle_overlay_factory(
            product["particle_manager_provider"],
        ),
        effect_overlay_factory=create_effect_overlay_factory(
            product["effect_manager_provider"],
            cleanup_effect_script_manager,
        ),
        tray_host_factory=create_tray_host_factory(
            get_tray_icon,
            cleanup_tray_icon,
        ),
        world_object_backend=QtWorldObjectBackend(_QT_WORLD_OBJECT_TYPES),
    )


def _configure_dx_backend() -> None:
    from lib.core.dx_bridge.desktop_backend import configure_dx_desktop_backend
    from lib.script.music.service import configure_music_player_factory

    configure_music_player_factory(None)
    configure_dx_desktop_backend(
        **_product_runtime_factories(),
        workbench_opener=_open_workbench,
    )


def configure_selected_desktop_backend():
    """Register Qt lazily and apply the configured backend selection."""
    from config.config import UI
    from lib.core.backend_router import configure_selected_backend, register_backend

    register_backend("qt", _configure_qt_backend)
    register_backend("directx", _configure_dx_backend)
    return configure_selected_backend(UI.get("render_backend", "qt"))
