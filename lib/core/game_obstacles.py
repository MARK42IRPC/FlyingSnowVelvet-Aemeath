"""Backend-neutral geometry exposed by an active game host."""
from __future__ import annotations

from collections.abc import Callable
import threading

from lib.core.graphics.types import Rect


GameObstacleProvider = Callable[[], Rect | None]

_provider: GameObstacleProvider | None = None
_provider_lock = threading.RLock()


def configure_game_obstacle_provider(provider: GameObstacleProvider | None) -> None:
    """Install the geometry provider owned by the active desktop game host."""
    if provider is not None and not callable(provider):
        raise TypeError("game obstacle provider must be callable")
    global _provider
    with _provider_lock:
        _provider = provider


def reset_game_obstacle_provider(
    expected: GameObstacleProvider | None = None,
) -> None:
    """Clear the provider, optionally only when it is still the expected owner."""
    global _provider
    with _provider_lock:
        if expected is None or _provider is expected:
            _provider = None


def get_game_obstacle_rect() -> Rect | None:
    """Return the current desktop obstacle without importing a UI toolkit."""
    with _provider_lock:
        provider = _provider
    if provider is None:
        return None
    rect = provider()
    if not isinstance(rect, Rect) or rect.width <= 0 or rect.height <= 0:
        return None
    return rect


__all__ = [
    "GameObstacleProvider",
    "configure_game_obstacle_provider",
    "get_game_obstacle_rect",
    "reset_game_obstacle_provider",
]
