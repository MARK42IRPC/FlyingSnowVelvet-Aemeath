"""Protocols for pluggable graphics backends."""
from __future__ import annotations

from typing import Protocol

from .scene import DrawScene


class DrawBackend(Protocol):
    def render(self, scene: DrawScene, painter: object, target_rect: object | None = None) -> None:
        """Render the scene into a backend-owned target."""

    def cleanup(self) -> None:
        """Release backend caches and resources."""
