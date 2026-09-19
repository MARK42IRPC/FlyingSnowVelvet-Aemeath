"""QQ provider adapter."""

from __future__ import annotations

from lib.core.logger import get_logger
from lib.script.qqmusic import get_qqmusic_client

from ..provider import MusicProvider
from ..types import MusicTrack
from ._shared import UNKNOWN_TITLE, extract_first_artist, format_duration_text

logger = get_logger(__name__)

#: QQ 原始字段名：`raw` 与平铺层都用 `singer`/`singers`/`artists` 这套写法。
_QQ_RAW_ARTIST_KEYS = ("singer", "singers", "artists", "artist")
_QQ_SONG_ARTIST_KEYS = ("singer", "singers", "artists", "artist")


class QQMusicProvider(MusicProvider):
    """QQ provider adapter based on QQmisic API client."""

    provider_name = "qq"
    provider_label = "QQ音乐"

    def __init__(self) -> None:
        self._api = get_qqmusic_client()

    @classmethod
    def _extract_first_artist(cls, song: dict) -> str:
        return extract_first_artist(
            song,
            raw_keys=_QQ_RAW_ARTIST_KEYS,
            song_keys=_QQ_SONG_ARTIST_KEYS,
        )

    def search(self, keyword: str, mode: str = "song", limit: int = 25) -> list[MusicTrack]:
        query = str(keyword or "").strip()
        if not query:
            return []
        normalized_mode = str(mode or "song").strip().lower()
        max_items = max(1, int(limit or 25))
        if normalized_mode not in {"song", "artist", "album", "playlist"}:
            normalized_mode = "song"
        if normalized_mode == "playlist":
            logger.info("[MusicProvider:QQ] playlist 搜索暂不返回不可播放的歌单 ID，keyword=%s", query)
            return []
        try:
            songs = self._api.search_song(query, page_num=1, num_per_page=max_items)
            tracks: list[MusicTrack] = []
            for song in songs:
                mid = str(song.get("mid") or "").strip()
                if not mid:
                    continue
                media_mid = str(song.get("media_mid") or mid).strip() or mid
                title = str(song.get("title") or UNKNOWN_TITLE).strip() or UNKNOWN_TITLE
                artist = self._extract_first_artist(song)
                duration_ms = song.get("duration_ms")
                normalized_duration = None
                try:
                    if duration_ms is not None:
                        normalized_duration = int(duration_ms)
                except (TypeError, ValueError):
                    normalized_duration = None
                display = f"{format_duration_text(normalized_duration)} {title} - {artist}"
                tracks.append(
                    MusicTrack(
                        provider=self.provider_name,
                        track_id=(
                            f"{self.provider_name}:{mid}:{media_mid}"
                            if media_mid and media_mid != mid
                            else f"{self.provider_name}:{mid}"
                        ),
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
            logger.error("[MusicProvider:QQ] 搜索失败 mode=%s keyword=%s: %s", normalized_mode, query, e)
            raise
