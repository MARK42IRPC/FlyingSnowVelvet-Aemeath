"""Qt adapter for the backend-neutral tray host contract."""
from __future__ import annotations

from collections.abc import Callable

from lib.core.qt_bridge.tray_icon import cleanup_tray_icon, get_tray_icon
from lib.core.tray_host import TrayCommandCallback, TrayHost, TrayMenuState


class QtTrayHost:
    """Hide Qt signals and singleton teardown behind ``TrayHost``."""

    def __init__(self, tray_icon=None) -> None:
        self._tray_icon = tray_icon if tray_icon is not None else get_tray_icon()

    def connect_quit_requested(self, callback: Callable[[], None]) -> None:
        self._tray_icon.quit_requested.connect(callback)

    def disconnect_quit_requested(self, callback: Callable[[], None]) -> None:
        try:
            self._tray_icon.quit_requested.disconnect(callback)
        except (TypeError, RuntimeError):
            pass

    def connect_announcement_requested(self, callback: Callable[[], None]) -> None:
        self._tray_icon.announcement_requested.connect(callback)

    def disconnect_announcement_requested(self, callback: Callable[[], None]) -> None:
        try:
            self._tray_icon.announcement_requested.disconnect(callback)
        except (TypeError, RuntimeError):
            pass

    def connect_command_requested(self, callback: TrayCommandCallback) -> None:
        self._tray_icon.command_requested.connect(callback)

    def disconnect_command_requested(self, callback: TrayCommandCallback) -> None:
        try:
            self._tray_icon.command_requested.disconnect(callback)
        except (TypeError, RuntimeError):
            pass

    def set_menu_state(self, state: TrayMenuState) -> None:
        self._tray_icon.set_menu_state(state)

    def initialize(self) -> bool:
        return bool(self._tray_icon.initialize())

    def begin_shutdown(self) -> None:
        self._tray_icon.begin_shutdown()

    def cleanup(self) -> None:
        cleanup_tray_icon()


def get_tray_host() -> TrayHost:
    return QtTrayHost()
