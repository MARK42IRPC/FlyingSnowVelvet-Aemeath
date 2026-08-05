"""Backend-neutral system tray host contract."""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


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

    def initialize(self) -> bool:
        """Create or restore the native tray representation."""

    def begin_shutdown(self) -> None:
        """Hide interactive tray UI before the rest of the app stops."""

    def cleanup(self) -> None:
        """Release tray resources and subscriptions."""


TrayHostFactory = Callable[[], TrayHost]
