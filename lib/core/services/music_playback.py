"""Backend-neutral contract for local music playback.

``cloudmusic`` drives exactly one local audio file at a time - play, pause,
resume, stop, volume, seek - and observes the result through callbacks. That
surface used to be spelled as Qt signals, so a composition without PyQt could
not inject a player at all and had to keep a second, parallel code path for
its own fallback player. This module keeps the same information flow but drops
the toolkit dependency, so every composition injects a player and the manager
drives a single path.

Implementations:

* ``lib/core/render/backends/qt/music_player.QtMusicPlayer`` - QtMultimedia, marshals
  commands onto the Qt main thread.
* ``lib/script/cloudmusic/_player.MciMusicPlayer`` - Windows MCI, no toolkit.

Commands are asynchronous: :meth:`MusicPlayerProtocol.play` returns as soon as
the request is accepted, and the outcome arrives through the callbacks
installed by :meth:`MusicPlayerProtocol.set_callbacks`. Callbacks run on
whichever thread the implementation owns, so handlers must be thread-safe.

Like the rest of ``lib/core`` this module must stay importable without PyQt.
"""
from __future__ import annotations

from typing import Callable, Optional, Protocol

#: ``(generation,)`` - playback started, or the track ended and the queue may advance.
PlayerEventCallback = Callable[[int], None]
#: ``(generation, message)`` - playback failed before or during the file.
PlayerErrorCallback = Callable[[int, str], None]
#: ``(generation, duration_ms)`` - the media duration became known.
PlayerDurationCallback = Callable[[int, int], None]


class MusicPlayerProtocol(Protocol):
    """Single-track local player driven by the cloudmusic manager."""

    def set_callbacks(
        self,
        *,
        on_started: Optional[PlayerEventCallback] = None,
        on_finished: Optional[PlayerEventCallback] = None,
        on_error: Optional[PlayerErrorCallback] = None,
        on_duration_changed: Optional[PlayerDurationCallback] = None,
    ) -> None:
        """Install the observing callbacks, replacing whichever were installed.

        Every argument defaults to ``None``; calling it with no argument at all
        therefore detaches every callback, which is how the manager
        unsubscribes before dropping a player.
        """

    def play(self, file_path: str, volume: float, generation: int) -> None:
        """Start ``file_path`` at ``volume`` (0.0-1.0), tagged with ``generation``.

        ``generation`` is echoed back on every callback so a late callback from
        an abandoned request can be ignored.
        """

    def pause(self) -> None:
        """Pause the current track; a no-op when nothing is playing."""

    def resume(self) -> None:
        """Resume a paused track; a no-op when nothing is paused."""

    def stop(self) -> None:
        """Stop playback, release the media and stay silent until the next play."""

    def set_volume(self, volume: float) -> None:
        """Apply ``volume`` (0.0-1.0) immediately, playing or paused."""

    def seek(self, position_ms: int) -> None:
        """Jump to ``position_ms``."""

    def is_busy(self) -> bool:
        """Return True while a track is playing or paused."""

    def position_ms(self) -> int:
        """Return the current position in milliseconds."""

    def duration_ms(self) -> int:
        """Return the media duration in milliseconds, 0 when still unknown."""

    def cleanup(self) -> None:
        """Release the player; must be idempotent and safe after a failure."""


__all__ = [
    "MusicPlayerProtocol",
    "PlayerDurationCallback",
    "PlayerErrorCallback",
    "PlayerEventCallback",
]
