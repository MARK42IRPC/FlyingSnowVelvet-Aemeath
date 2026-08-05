"""Backend-neutral application UI lifecycle contract."""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class ApplicationUiHost(Protocol):
    """Own application-level UI without exposing toolkit objects."""

    def configure_services(self, yuanbao_service: object) -> None:
        """Inject UI initializers required by long-lived services."""

    def prepare_application(self, application: object) -> None:
        """Initialize toolkit resources after the application exists."""

    def prepare_runtime(self) -> None:
        """Create runtime UI that must exist before APP_MAIN."""

    def start_runtime(self, application: object) -> None:
        """Start announcement and staged UI preloading."""

    def open_announcement(self) -> None:
        """Open the announcement through the active UI implementation."""

    def begin_shutdown(self) -> None:
        """Hide interactive UI at the start of application shutdown."""

    def stop_runtime(self) -> None:
        """Stop background UI work and primary dialogs."""

    def cleanup(self) -> None:
        """Release all runtime UI resources."""

    def has_exit_animation(self) -> bool:
        """Return whether APP_EXIT still has an animation consumer."""

    def finalize(self) -> None:
        """Release UI resources that must survive the event loop."""


ApplicationUiHostFactory = Callable[[], ApplicationUiHost]
