"""Protocols for pluggable graphics backends."""
from __future__ import annotations

from typing import Protocol

from .commands import DrawBatch
from .types import Rect


class DrawBackend(Protocol):
    def render(
        self,
        batch: DrawBatch,
        target: object,
        viewport: Rect | None = None,
    ) -> None:
        """Render one immutable command batch into a backend-owned target."""

    def cleanup(self) -> None:
        """Release backend caches and resources."""
