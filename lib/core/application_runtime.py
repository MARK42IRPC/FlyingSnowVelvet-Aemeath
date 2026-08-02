"""Backend-neutral application event-loop contract."""
from __future__ import annotations

from typing import Callable, Protocol


class ApplicationRuntime(Protocol):
    """Host operations required by the application lifecycle coordinator."""

    def create_application(
        self,
        logger: object,
        argv: list[str] | None = None,
    ) -> object:
        """Create the backend application object."""

    def connect_exit_acknowledged(
        self,
        application: object,
        callback: Callable[[], None],
    ) -> None:
        """Notify the coordinator when the event loop accepts an exit request."""

    def schedule_once(self, delay_ms: int, callback: Callable[[], None]) -> None:
        """Schedule one callback on the application event loop."""

    def run_event_loop(self, application: object) -> int:
        """Run the event loop and return its exit code."""

    def process_events(self, application: object) -> None:
        """Process pending application events."""

    def request_exit(self, application: object, exit_code: int) -> None:
        """Flush deferred destruction and request event-loop exit."""

    def close_all_windows(self, application: object) -> None:
        """Close remaining top-level windows before an exit retry."""
