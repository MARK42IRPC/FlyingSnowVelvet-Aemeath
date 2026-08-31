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
    from lib.script.SEanima.animation import (
        cleanup_start_exit_animation,
        get_start_exit_animation,
    )
    from lib.script.app.wuwa_launcher import get_wuthering_waves_launcher
    from lib.script.music import get_music_service

    def world_object_sound_factory(object_type: str):
        if object_type == "motor":
            from lib.script.voice.chrack import ChrackSound
            return {"impact": ChrackSound()}
        if object_type == "clock":
            from lib.script.voice.gear import GearSound
            from lib.script.voice.ring import RingSound
            return {"impact": GearSound(), "countdown": RingSound()}
        if object_type in {"sofa", "speaker"}:
            from lib.script.voice.sofa import SofaSound
            return {"impact": SofaSound()}
        if object_type == "snowball":
            from lib.script.voice.snowball_sound import SnowballSound
            return {"impact": SnowballSound(), "action": SnowballSound()}
        if object_type in {"snow_pile", "snow_leopard"}:
            from lib.script.voice.snow import SnowSound
            return {"impact": SnowSound(), "action": SnowSound()}
        return {}

    return {
        "state_machine_factory": StateMachine,
        "startup_sound_factory": AmsStartupSound,
        "interaction_sound_factory": AmsEnhSound,
        "particle_manager_provider": get_particle_script_manager,
        "effect_manager_provider": get_effect_script_manager,
        "world_object_sound_factory": world_object_sound_factory,
        "launch_wuwa": get_wuthering_waves_launcher().launch,
        "animation_factory": get_start_exit_animation,
        "animation_cleanup": cleanup_start_exit_animation,
        "music_service_provider": get_music_service,
    }


def _open_workbench(initial_page: str) -> bool:
    from lib.script.app.workbench_helper import launch_workbench_helper

    return bool(launch_workbench_helper(initial_page=initial_page))


def _open_game_helper(action: str, game_id: str) -> bool:
    from lib.script.app.workbench_helper import launch_workbench_helper

    normalized_id = str(game_id or "").strip()
    helper_action = str(action or "").strip().lower()
    if normalized_id:
        if helper_action not in {"open", "close"}:
            return False
    else:
        helper_action = {
            "open": "open_manager",
            "close": "close_manager",
        }.get(helper_action, "")
        if not helper_action:
            return False
    return bool(launch_workbench_helper(
        initial_page="game_manager",
        game_id=normalized_id,
        game_action=helper_action,
    ))


def _create_game_command_runtime():
    from lib.script.gemes.MAIN.command_runtime import GameCommandRuntime
    from lib.script.gemes.MAIN.game_packages import (
        cleanup_game_package_service,
        get_game_package_service,
    )

    return GameCommandRuntime(
        get_game_package_service(),
        _open_game_helper,
        package_cleanup=cleanup_game_package_service,
    )


def _configure_qt_backend() -> None:
    from lib.core.qt_bridge.desktop_backend import configure_qt_desktop_backend
    from lib.core.qt_bridge.effect_system import create_effect_overlay_factory
    from lib.core.qt_bridge.particle_system import create_particle_overlay_factory
    from lib.core.qt_bridge.pet_window import create_qt_pet_window_factory
    from lib.core.qt_bridge.tray_host import create_tray_host_factory
    from lib.core.qt_bridge.world_object_backend import QtWorldObjectBackend
    from lib.core.qt_bridge.music_player import QtMusicPlayer
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
    from lib.core.dx_bridge.dpi_awareness import (
        ensure_per_monitor_v2_dpi_awareness,
    )
    from lib.core.dx_bridge.desktop_backend import configure_dx_desktop_backend
    from lib.script.music.service import configure_music_player_factory

    if not ensure_per_monitor_v2_dpi_awareness():
        raise RuntimeError("DirectX 后端无法启用 Per-Monitor V2 DPI 模式")
    configure_music_player_factory(None)
    configure_dx_desktop_backend(
        **_product_runtime_factories(),
        workbench_opener=_open_workbench,
        game_command_runtime_factory=_create_game_command_runtime,
    )


def configure_selected_desktop_backend():
    """Register Qt lazily and apply the configured backend selection."""
    from config.config import UI
    from lib.core.backend_router import configure_selected_backend, register_backend

    register_backend("qt", _configure_qt_backend)
    register_backend("directx", _configure_dx_backend)
    return configure_selected_backend(UI.get("render_backend", "qt"))
