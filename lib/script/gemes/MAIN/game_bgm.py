"""Game BGM requests routed through the desktop pet's shared music queue.

Only ``_search`` runs on a worker. Requests, draining and closing belong to
the host thread, so playback initialization and queue events stay on it.
"""

from __future__ import annotations

import queue
import re

from lib.core.event.center import Event, EventType
from lib.core.logger import get_logger
from lib.script.music.types import MusicTrack

_logger = get_logger(__name__)


def _track_ref(value) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("netease:"):
        text = text.split(":", 1)[1]
    try:
        number = int(text)
        return str(number & 0xFFFFFFFF if number < 0 else number)
    except ValueError:
        return text


class GameBgmController:
    """Own a game's current request, never a separate player or playlist."""

    def __init__(self, *, music_service, submit, publish) -> None:
        self._music_service = music_service
        self._submit = submit
        self._publish = publish
        self._completed = queue.SimpleQueue()
        self._cache: dict[tuple[str, str, str], object] = {}
        self._generation = 0
        self._active = False
        self._key: tuple[str, str, str] | None = None
        self._future = None
        self._track = None
        self._service = None
        self._force = False
        self._exclusive = False
        self._queue_prepared = False
        self._pending_play = False
        self._enqueue_requested = False
        self._owned_ref = ""

    def activate(self) -> None:
        self.close()
        self._active = True

    def request(self, keyword: str, artist: str = "", *, track_ref: str = "", force: bool = False, exclusive: bool = False) -> None:
        keyword, artist = str(keyword or "").strip(), str(artist or "").strip()
        track_ref = str(track_ref or "").strip()
        key = (keyword.casefold(), artist.casefold(), _track_ref(track_ref))
        if not self._active or not (keyword or track_ref) or (key == self._key and bool(exclusive) == self._exclusive):
            return
        self._generation += 1
        generation = self._generation
        if self._future is not None:
            self._future.cancel()
        self._key = key
        self._force = bool(force)
        self._exclusive = bool(exclusive)
        self._queue_prepared = False
        self._track = self._cache.get(key)
        self._pending_play = self._track is not None
        self._enqueue_requested = False
        # This method is called on the host thread, before worker submission.
        try:
            self._service = self._music_service()
            self._service.initialize()
        except Exception as exc:
            self._pending_play = False
            _logger.warning("[GameBGM] 音乐后端初始化失败: %s", exc)
            return
        if track_ref:
            # A supplied provider reference identifies the recording directly;
            # title punctuation and search availability cannot change it.
            provider = track_ref.split(":", 1)[0].casefold() if ":" in track_ref else "netease"
            self._track = MusicTrack(provider, track_ref, keyword or track_ref, artist)
            self._cache[key] = self._track
            self._pending_play = True
            return
        if self._track is not None:
            return
        try:
            future = self._submit(self._search, self._service, keyword, artist)
        except Exception as exc:
            _logger.warning("[GameBGM] 无法提交搜索 %s: %s", keyword, exc)
            return
        self._future = future
        future.add_done_callback(lambda result: self._completed.put((generation, key, result)))

    @staticmethod
    def _search(service, keyword: str, artist: str):
        tracks = service.search(keyword, mode="song", limit=20)
        title_key, artist_key = keyword.casefold(), artist.casefold()
        for track in tracks:
            if str(getattr(track, "title", "") or "").strip().casefold() != title_key:
                continue
            artists = {part.strip().casefold() for part in re.split(r"[/、,，&;；]", str(getattr(track, "artist", "") or ""))}
            if artist_key and artist_key not in artists:
                continue
            if _track_ref(getattr(track, "track_id", "")):
                return track
        return None

    def drain(self, *, can_play: bool = True) -> None:
        """Commit only current search results, then select the shared queue item.

        Enqueue events can be pending while a TICK is being dispatched. Wait
        until a later tick observes the real item before selecting its index.
        """
        if not self._active or not can_play:
            return
        while True:
            try:
                generation, key, future = self._completed.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation or key != self._key:
                continue
            try:
                track = future.result()
            except Exception as exc:
                _logger.warning("[GameBGM] 搜索失败 %s: %s", key[0], exc)
                continue
            if track is None:
                _logger.info("[GameBGM] 未找到指定歌名和作者: %s / %s", *key[:2])
                continue
            self._cache[key] = track
            self._track = track
            self._pending_play = True
        if not self._pending_play or self._track is None:
            return
        service = self._service
        if not self._force and not service.can_takeover_for_bgm():
            self._pending_play = False
            return
        if self._exclusive and not self._queue_prepared:
            # The game explicitly owns a one-song shared queue for this
            # phase. One item loops through the backend's normal play modes.
            service.clear_queue()
            self._queue_prepared = True
            self._enqueue_requested = False
        ref = _track_ref(self._track.track_id)
        songs = service.queue_snapshot()
        index = next((i for i, item in enumerate(songs) if _track_ref(item[0]) == ref), -1)
        if index < 0:
            if not self._enqueue_requested:
                self._enqueue_requested = bool(service.enqueue_track(
                    self._track, silent=True, autoplay=False,
                    request_is_current=self._play_guard(ref),
                ))
                if not self._enqueue_requested:
                    self._pending_play = False
            return
        if service.current_index() == index:
            if service.is_paused() or not service.is_playing():
                self._publish(Event(EventType.MUSIC_PLAY_PAUSE, {
                    "playing": True,
                    "request_is_current": self._play_guard(ref, require_current=True),
                }))
        else:
            self._publish(Event(EventType.MUSIC_PLAY_QUEUE_INDEX, {
                "index": index,
                "request_is_current": self._play_guard(ref, queue_index=index),
            }))
        self._owned_ref = ref
        self._pending_play = False

    def _current_ref(self) -> str:
        if self._service is None:
            return ""
        songs = self._service.queue_snapshot()
        index = self._service.current_index()
        return _track_ref(songs[index][0]) if 0 <= index < len(songs) else ""

    def _play_guard(self, ref: str, *, require_current: bool = False, queue_index: int | None = None):
        generation, key = self._generation, self._key

        def is_current() -> bool:
            if not self._active or generation != self._generation or key != self._key:
                return False
            if queue_index is not None:
                songs = self._service.queue_snapshot()
                if (not (0 <= queue_index < len(songs)) or _track_ref(songs[queue_index][0]) != ref
                        or (self._exclusive and len(songs) != 1)):
                    # User queue events ahead of this command can move/remove
                    # an item after publication. Never play the new occupant;
                    # resolve the song's actual index on the next host tick.
                    self._pending_play = True
                    self._enqueue_requested = False
                    self._queue_prepared = False
                    return False
            return not require_current or self._current_ref() == ref

        return is_current

    def close(self) -> None:
        """Invalidate work and pause only the song still owned by this game."""
        self._generation += 1
        self._active = False
        self._key = None
        self._track = None
        self._pending_play = False
        self._enqueue_requested = False
        self._queue_prepared = False
        if self._future is not None:
            self._future.cancel()
            self._future = None
        if self._owned_ref and self._service is not None:
            owned_ref, generation = self._owned_ref, self._generation
            if self._current_ref() == owned_ref:
                self._publish(Event(EventType.MUSIC_PLAY_PAUSE, {
                    "playing": False,
                    "request_is_current": lambda: (generation == self._generation and self._current_ref() == owned_ref),
                }))
        self._owned_ref = ""
