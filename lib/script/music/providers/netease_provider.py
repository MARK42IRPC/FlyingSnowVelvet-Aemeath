"""NetEase provider adapter."""

from __future__ import annotations

import re

from lib.core.logger import get_logger

from ..provider import MusicProvider
from ..types import MusicTrack

logger = get_logger(__name__)

_DURATION_TEXT_RE = re.compile(r"^\s*(\d{1,3}):(\d{2})\s*$")
_SEARCH_MODE_TYPES = {
    "song": 1,
    "artist": 100,
    "album": 10,
    "playlist": 1000,
}
_NETEASE_SEARCH_PAGE_SIZE = 50
_NETEASE_MAX_SEARCH_PAGES = 12
_NETEASE_SEARCH_RETRY_COUNT = 2


class NetEaseMusicProvider(MusicProvider):
    """Provider adapter based on pyncm."""

    provider_name = "netease"
    provider_label = "NetEase Music"

    @staticmethod
    def _normalize_song_id(song_id) -> int | None:
        try:
            sid = int(song_id)
        except (TypeError, ValueError):
            return None
        return sid & 0xFFFFFFFF if sid < 0 else sid

    @classmethod
    def _make_track_ref(cls, song_id) -> str | None:
        sid = cls._normalize_song_id(song_id)
        if sid is None:
            return None
        return f"{cls.provider_name}:{sid}"

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
    def _extract_first_artist(song: dict) -> str:
        artists = song.get("ar") or song.get("artists") or []
        if not artists:
            return "Unknown Artist"
        first = artists[0]
        if isinstance(first, dict):
            name = str(first.get("name") or "").strip()
            return name or "Unknown Artist"
        name = str(first).strip()
        return name or "Unknown Artist"

    def _song_to_track(self, song: dict) -> MusicTrack | None:
        track_ref = self._make_track_ref(song.get("id"))
        if track_ref is None:
            return None
        title = str(song.get("name") or "鏈煡姝屾洸").strip() or "鏈煡姝屾洸"
        artist = self._extract_first_artist(song)
        duration_ms = song.get("dt") or song.get("duration")
        display = f"{self._format_duration_text(duration_ms)} {title} - {artist}"
        normalized_duration = None
        try:
            if duration_ms is not None:
                normalized_duration = int(duration_ms)
        except (TypeError, ValueError):
            normalized_duration = None
        return MusicTrack(
            provider=self.provider_name,
            track_id=track_ref,
            title=title,
            artist=artist,
            duration_ms=normalized_duration,
            display=display,
            raw=song,
        )

    @staticmethod
    def _is_cookie_conflict_error(error: Exception) -> bool:
        text = str(error or "")
        if not text:
            return False
        lower = text.lower()
        return "__csrf" in lower and "multiple cookies" in lower

    @staticmethod
    def _clear_runtime_login_cookies() -> None:
        try:
            from pyncm.apis.login import GetCurrentSession

            session = GetCurrentSession()
            jar = getattr(session, "cookies", None)
            if jar is None:
                return
            try:
                jar.clear()
                return
            except Exception:
                pass
            for cookie in list(jar):
                try:
                    jar.clear(domain=cookie.domain, path=cookie.path, name=cookie.name)
                except Exception:
                    continue
        except Exception:
            return

    def _recover_search_session(self) -> bool:
        try:
            from pyncm.apis.login import LoginViaAnonymousAccount
        except Exception:
            return False
        try:
            self._clear_runtime_login_cookies()
            LoginViaAnonymousAccount()
            logger.info("[MusicProvider:NetEase] Search session rebuilt (anonymous)")
            return True
        except Exception as e:
            logger.warning("[MusicProvider:NetEase] 鎼滅储浼氳瘽閲嶅缓澶辫触: %s", e)
            return False

    def _search_call(self, apis, keyword: str, stype: int, limit: int, offset: int = 0) -> dict:
        query = str(keyword or "").strip()
        search_limit = max(1, int(limit or 1))
        search_offset = max(0, int(offset or 0))
        last_result: dict = {}

        for attempt in range(_NETEASE_SEARCH_RETRY_COUNT):
            try:
                result = apis.cloudsearch.GetSearchResult(
                    query,
                    stype=int(stype),
                    limit=search_limit,
                    offset=search_offset,
                )
            except Exception as e:
                if attempt == 0 and self._is_cookie_conflict_error(e):
                    logger.warning("[MusicProvider:NetEase] 妫€娴嬪埌 __csrf Cookie 鍐茬獊锛屾竻鐞嗗悗閲嶈瘯")
                    self._clear_runtime_login_cookies()
                    continue
                raise

            last_result = result if isinstance(result, dict) else {}
            payload = (last_result.get("result") or {}) if isinstance(last_result, dict) else {}
            songs = payload.get("songs") or []
            if songs:
                return last_result
            # 浠呭棣栧睆绌虹粨鏋滃仛涓€娆′細璇濇仮澶嶏紝閬垮厤鍒嗛〉鍦烘櫙琚敊璇共棰勩€?
            if attempt == 0 and search_offset == 0 and self._recover_search_session():
                continue
            return last_result

        return last_result

    def _search_song_items(self, apis, keyword: str, limit: int) -> list[dict]:
        target = max(1, int(limit or 1))
        page_size = min(_NETEASE_SEARCH_PAGE_SIZE, target)
        max_pages = max(1, min(_NETEASE_MAX_SEARCH_PAGES, (target + page_size - 1) // page_size))
        songs: list[dict] = []
        seen_ids: set[int] = set()

        for page in range(max_pages):
            remain = target - len(songs)
            if remain <= 0:
                break
            req_limit = min(page_size, remain)
            offset = page * page_size
            result = self._search_call(
                apis,
                keyword,
                stype=_SEARCH_MODE_TYPES["song"],
                limit=req_limit,
                offset=offset,
            )
            page_items = (result.get("result") or {}).get("songs", []) or []
            if not page_items:
                break
            for song in page_items:
                try:
                    sid = int(song.get("id"))
                except Exception:
                    sid = None
                if sid is not None and sid in seen_ids:
                    continue
                if sid is not None:
                    seen_ids.add(sid)
                songs.append(song)
                if len(songs) >= target:
                    return songs[:target]
            if len(page_items) < req_limit:
                break
        return songs[:target]

    def _search_priority_items(self, apis, keyword: str, mode: str, limit: int) -> list[dict]:
        if mode == "song":
            return self._search_song_items(apis, keyword, limit=max(1, limit))

        search_limit = max(10, min(50, limit))
        result = self._search_call(
            apis,
            keyword,
            stype=_SEARCH_MODE_TYPES.get(mode, _SEARCH_MODE_TYPES["song"]),
            limit=search_limit,
            offset=0,
        )
        payload = result.get("result") or {}

        if mode == "artist":
            artists = payload.get("artists") or []
            artist_id = (artists[0] if artists else {}).get("id")
            if artist_id is None:
                return self._search_song_items(apis, keyword, limit=max(1, limit))
            try:
                detail = apis.artist.GetArtistTracks(
                    str(artist_id),
                    limit=max(1, limit),
                    order="hot",
                )
                songs = detail.get("songs") or []
                return songs[:max(1, limit)]
            except Exception:
                return self._search_song_items(apis, keyword, limit=max(1, limit))

        if mode == "album":
            albums = payload.get("albums") or []
            album_id = (albums[0] if albums else {}).get("id")
            if album_id is None:
                return self._search_song_items(apis, keyword, limit=max(1, limit))
            try:
                detail = apis.album.GetAlbumInfo(str(album_id))
                songs = detail.get("songs") or ((detail.get("album") or {}).get("songs") or [])
                return songs[:max(1, limit)]
            except Exception:
                return self._search_song_items(apis, keyword, limit=max(1, limit))

        if mode == "playlist":
            playlists = payload.get("playlists") or []
            playlist_id = (playlists[0] if playlists else {}).get("id")
            if playlist_id is None:
                return self._search_song_items(apis, keyword, limit=max(1, limit))
            try:
                detail = apis.playlist.GetPlaylistAllTracks(
                    int(playlist_id),
                    offset=0,
                    limit=max(1, limit),
                )
                songs = detail.get("songs") or []
                return songs[:max(1, limit)]
            except Exception:
                return self._search_song_items(apis, keyword, limit=max(1, limit))

        return self._search_song_items(apis, keyword, limit=max(1, limit))

    def search(self, keyword: str, mode: str = "song", limit: int = 25) -> list[MusicTrack]:
        query = str(keyword or "").strip()
        if not query:
            return []
        max_items = max(1, int(limit or 25))
        normalized_mode = str(mode or "song").strip().lower()
        if normalized_mode not in _SEARCH_MODE_TYPES:
            normalized_mode = "song"
        try:
            from pyncm import apis

            songs = self._search_priority_items(apis, query, normalized_mode, max_items)
            tracks: list[MusicTrack] = []
            for song in songs:
                track = self._song_to_track(song)
                if track is None:
                    continue
                tracks.append(track)
                if len(tracks) >= max_items:
                    break
            return tracks
        except Exception as e:
            logger.error("[MusicProvider:NetEase] 鎼滅储澶辫触 mode=%s keyword=%s: %s", normalized_mode, query, e)
            raise
