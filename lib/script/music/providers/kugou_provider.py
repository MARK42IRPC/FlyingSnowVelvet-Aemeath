"""Kugou provider adapter."""

from __future__ import annotations

from lib.core.logger import get_logger
from lib.script.kugou import get_kugou_client

from ..provider import MusicProvider
from ..types import MusicTrack
from ._shared import UNKNOWN_TITLE, extract_first_artist, format_duration_text

logger = get_logger(__name__)

#: 酷狗原始字段名：`raw` 层用 `SingerName` 一类写法，平铺层用 `authors`/`artist`。
_KUGOU_RAW_ARTIST_KEYS = ("authors", "SingerName", "singername", "singer_name", "author_name", "artist", "singer")
_KUGOU_SONG_ARTIST_KEYS = ("authors", "artist", "singer")


class KugouMusicProvider(MusicProvider):
    """Provider adapter based on Kugou API client."""

    provider_name = "kugou"
    provider_label = "酷狗音乐"

    def __init__(self) -> None:
        self._api = get_kugou_client()

    @classmethod
    def _extract_first_artist(cls, song: dict) -> str:
        return extract_first_artist(
            song,
            raw_keys=_KUGOU_RAW_ARTIST_KEYS,
            song_keys=_KUGOU_SONG_ARTIST_KEYS,
        )

    def search(self, keyword: str, mode: str = "song", limit: int = 25) -> list[MusicTrack]:
        query = str(keyword or "").strip()
        if not query:
            return []
        normalized_mode = str(mode or "song").strip().lower()
        max_items = max(1, int(limit or 25))
        if normalized_mode not in {"song", "artist", "album", "playlist"}:
            normalized_mode = "song"
        try:
            songs = self._api.search_song(query, page_num=1, num_per_page=max_items)
            tracks: list[MusicTrack] = []
            for song in songs:
                song_hash = str(song.get("hash") or "").strip()
                if not song_hash:
                    continue
                title = str(song.get("title") or UNKNOWN_TITLE).strip() or UNKNOWN_TITLE
                artist = self._extract_first_artist(song)
                duration_ms = song.get("duration_ms")
                normalized_duration = None
                try:
                    if duration_ms is not None:
                        normalized_duration = int(duration_ms)
                except (TypeError, ValueError):
                    normalized_duration = None
                try:
                    album_id = int(song.get("album_id") or 0)
                except (TypeError, ValueError):
                    album_id = 0
                try:
                    audio_id = int(song.get("album_audio_id") or song.get("audio_id") or 0)
                except (TypeError, ValueError):
                    audio_id = 0
                encode_mix = str(song.get("encode_album_audio_id") or "").strip()
                if encode_mix:
                    track_id = (
                        f"{self.provider_name}:{song_hash}:{max(0, album_id)}:{max(0, audio_id)}:{encode_mix}"
                    )
                elif album_id > 0 or audio_id > 0:
                    track_id = f"{self.provider_name}:{song_hash}:{max(0, album_id)}:{max(0, audio_id)}"
                else:
                    track_id = f"{self.provider_name}:{song_hash}"
                display = f"{format_duration_text(normalized_duration)} {title} - {artist}"
                tracks.append(
                    MusicTrack(
                        provider=self.provider_name,
                        track_id=track_id,
                        title=title,
                        artist=artist,
                        duration_ms=normalized_duration,
                        display=display,
                        raw=song,
                    )
                )
                if len(tracks) >= max_items:
                    break
            return tracks
        except Exception as e:
            logger.error("[MusicProvider:Kugou] 搜索失败 mode=%s keyword=%s: %s", normalized_mode, query, e)
            raise
