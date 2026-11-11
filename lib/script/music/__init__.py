"""Music abstraction public API."""

from .service import (
    MusicService,
    clear_all_history_and_login_data,
    cleanup_music_service,
    get_music_service,
)
from .types import MusicTrack
from .live_song_requests import (
    LiveProbeResult,
    cleanup_live_song_request_service,
    get_live_song_request_service,
    probe_live_room,
)

__all__ = [
    "MusicService",
    "MusicTrack",
    "clear_all_history_and_login_data",
    "get_music_service",
    "cleanup_music_service",
    "get_live_song_request_service",
    "cleanup_live_song_request_service",
    "LiveProbeResult",
    "probe_live_room",
]

