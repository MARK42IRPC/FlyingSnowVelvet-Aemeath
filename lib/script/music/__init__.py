"""Music abstraction public API."""

from .service import (
    MusicService,
    clear_all_history_and_login_data,
    cleanup_music_service,
    get_music_service,
)
from .types import MusicTrack

__all__ = [
    "MusicService",
    "MusicTrack",
    "clear_all_history_and_login_data",
    "get_music_service",
    "cleanup_music_service",
]

