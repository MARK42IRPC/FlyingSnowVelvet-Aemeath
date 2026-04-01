"""Music provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .types import MusicTrack


class MusicProvider(ABC):
    """Provider interface for external music platform integrations."""

    provider_name: str = ""
    provider_label: str = ""
    supported_modes: frozenset[str] = frozenset({"song", "artist", "album", "playlist"})

    @abstractmethod
    def search(self, keyword: str, mode: str = "song", limit: int = 25) -> list[MusicTrack]:
        """Search tracks from provider and return normalized results."""

    def supports_mode(self, mode: str) -> bool:
        normalized = str(mode or "song").strip().lower() or "song"
        return normalized in self.supported_modes

    def get_capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "label": self.provider_label or self.provider_name,
            "search_modes": tuple(sorted(self.supported_modes)),
        }

