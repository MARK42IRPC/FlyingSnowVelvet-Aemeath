"""Backend-neutral system tray host contract."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol


class TrayCommand(IntEnum):
    """Stable command identifiers shared by toolkit and native trays."""

    ANNOUNCEMENT = 1
    QUIT = 2
    OPEN_CMD = 3
    OPEN_SETTINGS = 11
    TOGGLE_GAME_MODE = 4
    TOGGLE_CLICKTHROUGH = 5
    TOGGLE_AUTOSTART = 6
    CLEANUP_DESKTOP = 7
    CLEANUP_CACHE = 8
    CLEANUP_HISTORY = 9
    OPEN_AUTHOR_PAGE = 10


@dataclass(frozen=True)
class TrayMenuState:
    """Checked state for tray options whose value can change at runtime."""

    game_mode_enabled: bool = False
    clickthrough_enabled: bool = False
    autostart_enabled: bool = False


TrayCommandCallback = Callable[[TrayCommand, bool | None], None]


class TrayHost(Protocol):
    """Small tray surface required by the application lifecycle."""

    def connect_quit_requested(self, callback: Callable[[], None]) -> None:
        """Subscribe to a user-requested application exit."""

    def disconnect_quit_requested(self, callback: Callable[[], None]) -> None:
        """Remove a previously registered exit callback."""

    def connect_announcement_requested(self, callback: Callable[[], None]) -> None:
        """Subscribe to a manual announcement request."""

    def disconnect_announcement_requested(self, callback: Callable[[], None]) -> None:
        """Remove a previously registered announcement callback."""

    def connect_command_requested(self, callback: TrayCommandCallback) -> None:
        """Subscribe to a backend-neutral tray command."""

    def disconnect_command_requested(self, callback: TrayCommandCallback) -> None:
        """Remove a previously registered command callback."""

    def set_menu_state(self, state: TrayMenuState) -> None:
        """Synchronize checkable menu items with authoritative app state."""

    def initialize(self) -> bool:
        """Create or restore the native tray representation."""

    def begin_shutdown(self) -> None:
        """Hide interactive tray UI before the rest of the app stops."""

    def cleanup(self) -> None:
        """Release tray resources and subscriptions."""


TrayHostFactory = Callable[[], TrayHost]
