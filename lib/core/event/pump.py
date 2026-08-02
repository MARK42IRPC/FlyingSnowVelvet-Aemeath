"""Backend-neutral event pump contract."""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class EventPump(Protocol):
    def emit(self) -> None:
        """Schedule the callback on the owning thread."""

    def disconnect(self) -> None:
        """Release the callback and backend resources."""


EventPumpFactory = Callable[[Callable[[], None]], EventPump]
