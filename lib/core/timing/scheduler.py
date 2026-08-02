"""Backend-neutral scheduling contracts used by the timing core."""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class PeriodicTimer(Protocol):
    """A cancellable timer that repeatedly invokes one callback."""

    def start(self, interval_ms: int) -> None:
        """Start or restart the timer with the given interval."""

    def stop(self) -> None:
        """Stop future callback delivery."""

    def set_interval(self, interval_ms: int) -> None:
        """Change the interval, preserving the current active state."""

    @property
    def interval_ms(self) -> int:
        """Return the currently configured interval."""

    @property
    def active(self) -> bool:
        """Return whether the timer is currently running."""

    def cleanup(self) -> None:
        """Release callback and backend resources."""


class Scheduler(Protocol):
    """Factory and owner for periodic timers used by one component."""

    def create_periodic_timer(
        self,
        callback: Callable[[], None],
    ) -> PeriodicTimer:
        """Create a stopped periodic timer for ``callback``."""

    def cleanup(self) -> None:
        """Stop and release every timer created by this scheduler."""
