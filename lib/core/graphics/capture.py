"""Backend-neutral screen capture contract."""
from __future__ import annotations

from typing import Protocol


class ScreenCapture(Protocol):
    """Capture desktop pixels without exposing backend image objects."""

    def capture_primary_png(self) -> bytes | None:
        """Return the primary screen as PNG bytes, or ``None`` on failure."""
