"""Kugou provider adapter."""

from __future__ import annotations

import re

from lib.core.logger import get_logger
from lib.script.kugou import get_kugou_client

from ..provider import MusicProvider
from ..types import MusicTrack

logger = get_logger(__name__)
_DURATION_TEXT_RE = re.compile(r"^\s*(\d{1,3}):(\d{2})\s*$")
_ARTIST_TEXT_SPLIT_RE = re.compile(r"\s*(?:、|，|,|&|＆|;|；|\bfeat\.?\b|\bft\.?\b)\s*", re.IGNORECASE)
_UNKNOWN_TITLE = "未知歌曲"
_UNKNOWN_ARTIST = "未知作者"


class KugouMusicProvider(MusicProvider):
    """Provider adapter based on Kugou API client."""

    provider_name = "kugou"
    provider_label = "酷狗音乐"

    def __init__(self) -> None:
        self._api = get_kugou_client()

    @staticmethod
    def _format_duration_text(duration_ms) -> str:
        try:
            if isinstance(duration_ms, str):
                m = _DURATION_TEXT_RE.match(duration_ms)
                if m:
                    total_sec = int(m.group(1)) * 60 + int(m.group(2))
                else:
                    total_sec = max(0, int(float(duration_ms)) // 1000)
            elif isinstance(duration_ms, dict):
                raw = (
                    duration_ms.get("duration_ms")
                    or duration_ms.get("duration")
                    or duration_ms.get("dt")
                    or duration_ms.get("ms")
                )
                total_sec = max(0, int(raw) // 1000) if raw is not None else 0
            else:
                total_sec = max(0, int(duration_ms) // 1000)
        except (TypeError, ValueError):
            return "00:00"
        mins, secs = divmod(total_sec, 60)
        return f"{mins:02d}:{secs:02d}"

    @staticmethod
    def _looks_like_single_slash_name(left: str, right: str) -> bool:
        return (
            left.isascii()
            and right.isascii()
            and left.upper() == left
            and right.upper() == right
            and len(left) <= 4
            and len(right) <= 4
        )

    @classmethod
    def _split_first_artist_text(cls, artist_text) -> str:
        text = str(artist_text or "").strip()
        if not text:
            return ""
        parts = [part.strip() for part in _ARTIST_TEXT_SPLIT_RE.split(text) if part.strip()]
        text = parts[0] if parts else text
        if "/" not in text:
            return text
        left, right = [part.strip() for part in text.split("/", 1)]
        if not left or not right or cls._looks_like_single_slash_name(left, right):
            return text
        return left

    @classmethod
    def _extract_artist_name(cls, raw_artist) -> str:
        if isinstance(raw_artist, list):
            for item in raw_artist:
                name = cls._extract_artist_name(item)
                if name:
                    return name
            return ""
        if isinstance(raw_artist, dict):
            for key in ("name", "title", "artist"):
                name = cls._split_first_artist_text(raw_artist.get(key))
                if name:
                    return name
            return ""
        return cls._split_first_artist_text(raw_artist)

    @classmethod
    def _extract_first_artist(cls, song: dict) -> str:
        raw = song.get("raw")
        candidates = []
        if isinstance(raw, dict):
            candidates.extend(
                raw.get(key)
                for key in ("authors", "SingerName", "singername", "singer_name", "author_name", "artist", "singer")
            )
        candidates.extend(song.get(key) for key in ("authors", "artist", "singer"))
        for candidate in candidates:
            artist = cls._extract_artist_name(candidate)
            if artist:
                return artist
        return _UNKNOWN_ARTIST

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
                title = str(song.get("title") or _UNKNOWN_TITLE).strip() or _UNKNOWN_TITLE
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
                display = f"{self._format_duration_text(normalized_duration)} {title} - {artist}"
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
