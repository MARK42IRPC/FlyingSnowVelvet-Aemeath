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


class NullDrawBackend:
    """No-op backend used before a desktop backend is configured.

    A widget may legitimately be constructed and painted outside a running
    application (tests, the isolated workbench helper). Drawing nothing keeps
    that path alive without teaching the widget which toolkit is installed.
    """

    def render(
        self,
        batch: DrawBatch,
        target: object,
        viewport: Rect | None = None,
    ) -> None:
        return None

    def cleanup(self) -> None:
        return None
