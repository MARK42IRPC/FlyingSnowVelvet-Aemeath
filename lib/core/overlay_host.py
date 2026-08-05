"""Backend-neutral visual overlay lifecycle contract."""
from __future__ import annotations

from typing import Protocol


class OverlayHost(Protocol):
    """Lifecycle surface shared by particle and effect overlays."""

    def flush_immediately(self) -> None:
        """Clear visible overlay content without unsubscribing it."""

    def cleanup(self) -> None:
        """Stop delivery and release native overlay resources."""
