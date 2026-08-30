"""Qt application UI lifecycle adapter."""
from __future__ import annotations

from lib.core.application_ui import ApplicationUiHost


class QtApplicationUiHost:
    """Own Qt runtime UI and keep it out of the application coordinator."""

    def __init__(self) -> None:
        from lib.script.SEanima.animation import get_start_exit_animation

        self._animation = get_start_exit_animation()
        self._announcement_controller = None
        self._office_approval_controller = None
        self._preloader = None
        self._runtime_prepared = False
        self._runtime_started = False
        self._runtime_stopped = False
        self._runtime_cleaned = False
        self._finalized = False

    def prepare_application(self, application: object) -> None:
        from lib.core.qt_bridge.font import init_font_config

        init_font_config()

    def prepare_runtime(self) -> None:
        if self._runtime_prepared:
            return
        self._runtime_prepared = True

        from lib.script.ui.cmd_window import get_cmd_window
        from lib.script.ui.tooltip_panel import init_tooltip_panel
        from lib.script.gemes import get_game_runtime

        get_game_runtime()
        init_tooltip_panel()
        get_cmd_window()

    def start_runtime(self, application: object) -> None:
        if self._runtime_started:
            return
        self._runtime_started = True
        self._runtime_stopped = False

        from lib.script.ui.announcement_dialog import AnnouncementController
        from lib.script.ui.preloader import preload_runtime_ui
        from lib.script.ui.office_approval_controller import (
            OfficeApprovalController,
        )

        self._office_approval_controller = OfficeApprovalController()
        self._office_approval_controller.start()
        self._announcement_controller = AnnouncementController(application)
        self._preloader = preload_runtime_ui()
        self._announcement_controller.start()

    def open_announcement(self) -> None:
        if self._announcement_controller is not None:
            self._announcement_controller.open_from_tray()

    def open_settings(self) -> None:
        from lib.script.ui.tray_icon import get_tray_icon

        tray = get_tray_icon()
        if tray is None or not tray.initialize():
            raise RuntimeError('Qt 托盘不可用')
        tray.open_settings()

    def begin_shutdown(self) -> None:
        from lib.script.ui.shutdown import hide_all_runtime_ui

        hide_all_runtime_ui()

    def stop_runtime(self) -> None:
        if self._runtime_stopped:
            return
        self._runtime_stopped = True

        if self._office_approval_controller is not None:
            self._office_approval_controller.cleanup()
            self._office_approval_controller = None
        if self._preloader is not None:
            self._preloader.stop()
            self._preloader = None
        if self._announcement_controller is not None:
            self._announcement_controller.cleanup()
            self._announcement_controller = None

        from lib.script.gemes import cleanup_game_runtime

        cleanup_game_runtime()

    def cleanup(self) -> None:
        if self._runtime_cleaned:
            return
        self._runtime_cleaned = True
        self.stop_runtime()

        from lib.script.ui.shutdown import cleanup_all_runtime_ui

        cleanup_all_runtime_ui()

    def has_exit_animation(self) -> bool:
        return self._animation is not None

    def finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True

        from lib.script.SEanima.animation import cleanup_start_exit_animation

        cleanup_start_exit_animation()
        self._animation = None


def create_application_ui_host() -> ApplicationUiHost:
    return QtApplicationUiHost()
