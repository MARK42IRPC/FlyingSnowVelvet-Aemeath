"""Kugou music API client.

Provides search, track metadata, playable URL lookup, and QR login helpers.
"""

import base64
import contextlib
import hashlib
import html
import io
import random
import re
import string
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qsl
from typing import Any

import requests

from lib.core.logger import get_logger
from lib.script.browser_auth import launch_playwright_chromium

logger = get_logger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HASH_RE = re.compile(r"^[0-9a-fA-F]{24,64}$")


class KugouClient:
    """Kugou API wrapper for search / metadata / playable URL / QR login."""

    _SEARCH_URL = "https://songsearch.kugou.com/song_search_v2"
    _PLAYINFO_URL = "https://m.kugou.com/app/i/getSongInfo.php"
    _PLAY_SONGINFO_V2_URL = "https://wwwapi.kugou.com/play/songinfo"
    _PLAY_SONGINFO_V2_RETRY_URL = "https://wwwapiretry.kugou.com/play/songinfo"
    _FAVOR_SONG_LIST_URL = "https://gateway.kugou.com/v2/favor/song/list"
    _FAVOR_SONG_LIST_RETRY_URL = "https://gatewayretry.kugou.com/v2/favor/song/list"
    _FAVOR_RETRY_TIMES = 3
    _FAVOR_RETRY_BACKOFF = 0.35
    _QR_KEY_URL = "https://login-user.kugou.com/v2/qrcode"
    _QR_CHECK_URL = "https://login-user.kugou.com/v2/get_userinfo_qrcode"
    _COOKIE_DOMAINS = (
        ".kugou.com",
        ".www.kugou.com",
        ".m.kugou.com",
        ".login-user.kugou.com",
        ".gateway.kugou.com",
        ".gatewayretry.kugou.com",
        ".wwwapi.kugou.com",
        ".wwwapiretry.kugou.com",
    )

    _WEB_SIGN_KEY = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
    _APPID = 1005
    _QR_APPID = 1001
    _SRC_APPID = 2919
    _CLIENTVER = 20489

    def __init__(self, timeout: tuple[float, float] = (8.0, 20.0)) -> None:
        self._timeout = timeout
        self._session = requests.Session()
        self._last_songinfo_meta: dict[str, Any] = {}
        self._last_liked_meta: dict[str, Any] = {}
        self._song_meta_cache: dict[str, dict[str, Any]] = {}
        self._musicdl_client = None
        self._musicdl_init_done = False
        self._musicdl_disabled_reason = ""
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.kugou.com/",
                "Origin": "https://www.kugou.com",
            }
        )
        self._mid = self._random_mid()
        self._ensure_identity_cookies()

    @staticmethod
    def _random_mid() -> str:
        return hashlib.md5(str(uuid.uuid4()).encode("utf-8")).hexdigest()

    @staticmethod
    def _random_dfid(length: int = 24) -> str:
        chars = string.ascii_letters + string.digits
        if length <= 0:
            length = 24
        return "".join(random.choice(chars) for _ in range(length))

    @staticmethod
    def _safe_int(raw, default: int = 0) -> int:
        try:
            return int(raw)
        except Exception:
            return default

    @staticmethod
    def _clean_text(raw: Any) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        text = _HTML_TAG_RE.sub("", text)
        text = html.unescape(text)
        return text.strip()

    @staticmethod
    def _normalize_hash(raw_hash: Any) -> str:
        text = str(raw_hash or "").strip()
        if not text or not _HASH_RE.fullmatch(text):
            return ""
        return text.upper()

    @staticmethod
    def _safe_text(raw: Any) -> str:
        return str(raw or "").strip()

    @staticmethod
    def _cmp_text(raw: Any) -> str:
        return "".join(str(raw or "").lower().split())

    def _remember_song_meta(
        self,
        song_hash: str | None,
        *,
        title: str | None = None,
        artist: str | None = None,
        duration_ms: int | None = None,
        album_id: int | None = None,
        album_audio_id: int | None = None,
        encode_album_audio_id: str | None = None,
        musicdl_url: str | None = None,
        raw_search: dict[str, Any] | None = None,
    ) -> None:
        hash_text = self._normalize_hash(song_hash)
        if not hash_text:
            return
        old = self._song_meta_cache.get(hash_text, {})
        new_meta: dict[str, Any] = dict(old)

        title_text = self._safe_text(title)
        artist_text = self._safe_text(artist)
        encode_text = self._safe_text(encode_album_audio_id)
        url_text = self._safe_text(musicdl_url)
        duration_val = self._safe_positive_int(duration_ms)
        album_val = self._safe_positive_int(album_id)
        audio_val = self._safe_positive_int(album_audio_id)

        if title_text:
            new_meta["title"] = title_text
        if artist_text:
            new_meta["artist"] = artist_text
        if duration_val > 0:
            new_meta["duration_ms"] = duration_val
        if album_val > 0:
            new_meta["album_id"] = album_val
        if audio_val > 0:
            new_meta["album_audio_id"] = audio_val
        if encode_text:
            new_meta["encode_album_audio_id"] = encode_text
        if url_text.startswith("http"):
            new_meta["musicdl_url"] = url_text
        if isinstance(raw_search, dict) and raw_search:
            new_meta["raw_search"] = dict(raw_search)
        self._song_meta_cache[hash_text] = new_meta

    def _update_last_songinfo_meta(self, **updates: Any) -> dict[str, Any]:
        meta = dict(self._last_songinfo_meta or {})
        meta.update(updates)
        self._last_songinfo_meta = meta
        return meta

    def _cached_musicdl_url(self, song_hash: str | None, *, record_hit: bool = True) -> str:
        hash_text = self._normalize_hash(song_hash)
        if not hash_text:
            return ""
        cached_url = self._safe_text((self._song_meta_cache.get(hash_text) or {}).get("musicdl_url"))
        if cached_url.startswith("http"):
            if record_hit:
                self._update_last_songinfo_meta(
                    musicdl_fallback_hit=True,
                    musicdl_cached_hit=True,
                )
            return cached_url
        return ""

    def _musicdl_url_fallback(self, song_hash: str | None) -> str:
        hash_text = self._normalize_hash(song_hash)
        if not hash_text:
            return ""
        musicdl_track = self._musicdl_fallback_track(hash_text)
        musicdl_url = self._safe_text(musicdl_track.get("url"))
        if not musicdl_url.startswith("http"):
            return ""
        self._remember_song_meta(
            hash_text,
            title=musicdl_track.get("title"),
            artist=musicdl_track.get("artist"),
            duration_ms=musicdl_track.get("duration_ms"),
            musicdl_url=musicdl_url,
        )
        self._update_last_songinfo_meta(
            legacy_fallback_hit=False,
            musicdl_fallback_hit=True,
            musicdl_keyword=self._safe_text(musicdl_track.get("keyword")),
        )
        logger.info("[KugouClient] musicdl fallback hit hash=%s", hash_text)
        return musicdl_url

    def _apply_musicdl_detail_fallback(self, detail: dict[str, Any], song_hash: str | None) -> bool:
        hash_text = self._normalize_hash(song_hash)
        if not isinstance(detail, dict) or detail.get("url") or not hash_text:
            return False
        musicdl_track = self._musicdl_fallback_track(hash_text)
        musicdl_url = self._safe_text(musicdl_track.get("url"))
        if not musicdl_url.startswith("http"):
            return False
        detail["url"] = musicdl_url
        detail["url_candidates"] = [musicdl_url]
        musicdl_title = self._safe_text(musicdl_track.get("title"))
        musicdl_artist = self._safe_text(musicdl_track.get("artist"))
        musicdl_duration = self._safe_positive_int(musicdl_track.get("duration_ms"))
        if detail.get("title") in {"", hash_text} and musicdl_title:
            detail["title"] = musicdl_title
        if detail.get("artist") in {"", "未知歌手"} and musicdl_artist:
            detail["artist"] = musicdl_artist
        if not detail.get("duration_ms") and musicdl_duration > 0:
            detail["duration_ms"] = musicdl_duration
        self._remember_song_meta(
            hash_text,
            title=detail.get("title"),
            artist=detail.get("artist"),
            duration_ms=detail.get("duration_ms"),
            musicdl_url=musicdl_url,
        )
        self._update_last_songinfo_meta(
            musicdl_fallback_hit=True,
            musicdl_keyword=self._safe_text(musicdl_track.get("keyword")),
        )
        return True

    def _musicdl_work_dir(self) -> str:
        root_dir = Path(__file__).resolve().parents[3]
        work_dir = root_dir / "resc" / "user" / "temp" / "kugou_musicdl"
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return str(work_dir)

    def _get_musicdl_client(self):
        if self._musicdl_init_done:
            return self._musicdl_client
        self._musicdl_init_done = True
        try:
            from musicdl.modules.sources.kugou import KugouMusicClient

            self._musicdl_client = KugouMusicClient(
                search_size_per_source=8,
                search_size_per_page=8,
                strict_limit_search_size_per_page=True,
                disable_print=True,
                work_dir=self._musicdl_work_dir(),
            )
            logger.info("[KugouClient] musicdl fallback initialized")
        except Exception as e:
            self._musicdl_client = None
            self._musicdl_disabled_reason = str(e)
            logger.warning("[KugouClient] musicdl fallback unavailable: %s", e)
        return self._musicdl_client

    def _musicdl_keywords(self, song_hash: str) -> list[str]:
        hash_text = self._normalize_hash(song_hash)
        meta = self._song_meta_cache.get(hash_text, {})
        title = self._safe_text(meta.get("title"))
        artist = self._safe_text(meta.get("artist"))

        if not title and hash_text:
            try:
                info = self._fetch_song_info(hash_text)
            except Exception:
                info = {}
            title = title or self._clean_text(info.get("songName") or info.get("fileName"))
            artist = artist or self._clean_text(info.get("singerName") or info.get("author_name"))
            self._remember_song_meta(hash_text, title=title, artist=artist)

        out: list[str] = []
        if title and artist:
            out.append(f"{title} {artist}")
        if title:
            out.append(title)
        if artist and title:
            out.append(f"{artist} {title}")
        if not out and hash_text:
            out.append(hash_text)

        uniq: list[str] = []
        seen: set[str] = set()
        for item in out:
            key = self._cmp_text(item)
            if not key or key in seen:
                continue
            seen.add(key)
            uniq.append(item)
        return uniq

    def _musicdl_pick_song(self, songs: list[Any], song_hash: str) -> Any:
        if not songs:
            return None
        hash_upper = self._normalize_hash(song_hash)
        meta = self._song_meta_cache.get(hash_upper, {})
        title_cmp = self._cmp_text(meta.get("title"))
        artist_cmp = self._cmp_text(meta.get("artist"))

        fallback = None
        for song in songs:
            identifier = self._normalize_hash(getattr(song, "identifier", ""))
            url = self._safe_text(getattr(song, "download_url", ""))
            if not url.startswith("http"):
                continue
            if identifier and identifier == hash_upper:
                return song
            song_name_cmp = self._cmp_text(getattr(song, "song_name", ""))
            singer_cmp = self._cmp_text(getattr(song, "singers", ""))
            if title_cmp and title_cmp == song_name_cmp and (not artist_cmp or artist_cmp in singer_cmp):
                return song
            if fallback is None:
                fallback = song
        return fallback

    def _musicdl_build_search_item(self, song_hash: str) -> dict[str, Any]:
        hash_upper = self._normalize_hash(song_hash)
        if not hash_upper:
            return {}
        meta = self._song_meta_cache.get(hash_upper, {})
        raw = meta.get("raw_search")
        if not isinstance(raw, dict):
            raw = {}

        file_hash = self._safe_text(raw.get("FileHash")) or hash_upper
        title = self._clean_text(raw.get("SongName")) or self._safe_text(meta.get("title"))
        artist = self._clean_text(raw.get("SingerName")) or self._safe_text(meta.get("artist"))
        duration_sec = self._safe_int(raw.get("Duration"), 0)
        if duration_sec <= 0:
            duration_sec = self._safe_int(meta.get("duration_ms"), 0) // 1000
        album_id = self._safe_positive_int(raw.get("AlbumID") or meta.get("album_id"))
        album_audio_id = self._safe_positive_int(raw.get("Audioid") or meta.get("album_audio_id"))
        trans_param = raw.get("trans_param") if isinstance(raw.get("trans_param"), dict) else {}
        album_name = self._clean_text(raw.get("AlbumName"))
        filename = self._clean_text(raw.get("FileName"))
        if not filename:
            filename = f"{artist} - {title}".strip(" -")

        return {
            "hash": file_hash.lower(),
            "album_id": album_id,
            "album_audio_id": album_audio_id,
            "songname": title,
            "songname_original": title,
            "singername": artist,
            "duration": duration_sec,
            "timelen": duration_sec * 1000 if duration_sec > 0 else 0,
            "album_name": album_name,
            "filename": filename,
            "trans_param": trans_param,
        }

    def _musicdl_track_from_song_info(self, song_info: Any, keyword: str) -> dict[str, Any]:
        url = self._safe_text(getattr(song_info, "download_url", ""))
        if not url.startswith("http"):
            return {}
        title = self._safe_text(getattr(song_info, "song_name", ""))
        artist = self._safe_text(getattr(song_info, "singers", ""))
        duration_s = self._safe_int(getattr(song_info, "duration_s", 0), 0)
        duration_ms = duration_s * 1000 if duration_s > 0 else None
        return {
            "url": url,
            "title": title,
            "artist": artist,
            "duration_ms": duration_ms,
            "keyword": keyword,
        }

    def _musicdl_fast_track(self, song_hash: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        hash_upper = self._normalize_hash(song_hash)
        if not hash_upper:
            return out
        client = self._get_musicdl_client()
        if client is None:
            return out
        search_item = self._musicdl_build_search_item(hash_upper)
        if not search_item:
            return out
        if not hasattr(client, "_parsewiththirdpartapis") or not hasattr(client, "_parsewithofficialapiv1"):
            return out

        request_overrides = {"timeout": max(self._timeout)}
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                song_info_flac = client._parsewiththirdpartapis(search_item, request_overrides=request_overrides)
                song_info = client._parsewithofficialapiv1(
                    search_item,
                    request_overrides=request_overrides,
                    song_info_flac=song_info_flac,
                    lossless_quality_is_sufficient=True,
                )
        except Exception as e:
            logger.debug("[KugouClient] musicdl fast path failed hash=%s: %s", hash_upper, e)
            return out

        track = self._musicdl_track_from_song_info(song_info, keyword="__hash_fast__")
        if not track and song_info_flac is not song_info:
            track = self._musicdl_track_from_song_info(song_info_flac, keyword="__hash_fast__")
        if not track:
            return out
        self._remember_song_meta(
            hash_upper,
            title=track.get("title"),
            artist=track.get("artist"),
            duration_ms=track.get("duration_ms"),
            musicdl_url=track.get("url"),
        )
        return track

    def _musicdl_fallback_track(self, song_hash: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        hash_upper = self._normalize_hash(song_hash)
        if not hash_upper:
            return out

        cached_url = self._safe_text((self._song_meta_cache.get(hash_upper) or {}).get("musicdl_url"))
        if cached_url.startswith("http"):
            out["url"] = cached_url
            return out

        fast_track = self._musicdl_fast_track(hash_upper)
        fast_url = self._safe_text(fast_track.get("url"))
        if fast_url.startswith("http"):
            return fast_track

        client = self._get_musicdl_client()
        if client is None:
            return out

        keyword_hit = ""
        try:
            for keyword in self._musicdl_keywords(hash_upper):
                keyword_hit = keyword
                try:
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                        io.StringIO()
                    ):
                        songs = client.search(
                            keyword,
                            num_threadings=1,
                            request_overrides={"timeout": max(self._timeout)},
                            rule={"pagesize": 8},
                        )
                except Exception as e:
                    logger.debug("[KugouClient] musicdl search failed keyword=%s: %s", keyword, e)
                    continue

                picked = self._musicdl_pick_song(songs if isinstance(songs, list) else [], hash_upper)
                if not picked:
                    continue
                url = self._safe_text(getattr(picked, "download_url", ""))
                if not url.startswith("http"):
                    continue

                title = self._safe_text(getattr(picked, "song_name", ""))
                artist = self._safe_text(getattr(picked, "singers", ""))
                duration_s = self._safe_int(getattr(picked, "duration_s", 0), 0)
                duration_ms = duration_s * 1000 if duration_s > 0 else 0
                self._remember_song_meta(
                    hash_upper,
                    title=title,
                    artist=artist,
                    duration_ms=duration_ms if duration_ms > 0 else None,
                    musicdl_url=url,
                )
                out = {
                    "url": url,
                    "title": title,
                    "artist": artist,
                    "duration_ms": duration_ms if duration_ms > 0 else None,
                    "keyword": keyword_hit,
                }
                return out
        except Exception as e:
            logger.debug("[KugouClient] musicdl fallback exception hash=%s: %s", hash_upper, e)
        return out

    def get_session(self) -> requests.Session:
        return self._session

    def _set_cookie(self, key: str, value: str, domain: str | None = None) -> None:
        name = str(key or "").strip()
        if not name:
            return
        text = str(value or "")
        try:
            if domain:
                self._session.cookies.set(name, text, domain=domain, path="/")
            else:
                self._session.cookies.set(name, text)
        except Exception:
            pass

    def _set_cookie_all_domains(self, key: str, value: str) -> None:
        self._set_cookie(key, value)
        for domain in self._COOKIE_DOMAINS:
            self._set_cookie(key, value, domain=domain)

    @staticmethod
    def _safe_positive_int(raw: Any) -> int:
        try:
            val = int(str(raw).strip())
        except Exception:
            return 0
        return val if val > 0 else 0

    @staticmethod
    def _parse_kugou_cookie(raw: Any) -> dict[str, str]:
        text = str(raw or "").strip()
        if not text:
            return {}
        out: dict[str, str] = {}
        for key, value in parse_qsl(text, keep_blank_values=True):
            k = str(key or "").strip()
            if not k:
                continue
            out[k] = str(value or "")
        return out

    def _kugou_cookie_payload(self) -> dict[str, str]:
        cookies = self.export_cookies()
        return self._parse_kugou_cookie(cookies.get("KuGoo"))

    def _login_identity(self) -> tuple[str, str]:
        cookies = self.export_cookies()
        ku = self._kugou_cookie_payload()

        token = str(cookies.get("token") or ku.get("t") or "").strip()
        userid = str(cookies.get("userid") or ku.get("KugooID") or "").strip()

        if userid == "0":
            userid = ""
        return token, userid

    def _current_mid(self) -> str:
        cookies = self.export_cookies()
        mid = str(cookies.get("kg_mid") or cookies.get("mid") or self._mid).strip()
        if len(mid) >= 16:
            return mid
        # Align identity cookies with the web client behavior.
        mid = self._random_mid()
        self._mid = mid
        self._set_cookie("kg_mid", mid)
        self._set_cookie("mid", mid)
        return mid

    def _current_dfid(self) -> str:
        cookies = self.export_cookies()
        dfid = str(cookies.get("kg_dfid") or cookies.get("dfid") or "").strip()
        if dfid and dfid != "-":
            return dfid
        dfid = self._random_dfid(24)
        self._set_cookie("kg_dfid", dfid)
        self._set_cookie("dfid", dfid)
        return dfid

    def _ensure_identity_cookies(self) -> None:
        mid = self._current_mid()
        if mid:
            self._set_cookie_all_domains("kg_mid", mid)
            self._set_cookie_all_domains("mid", mid)
        dfid = self._current_dfid()
        if dfid:
            self._set_cookie_all_domains("kg_dfid", dfid)
            self._set_cookie_all_domains("dfid", dfid)

    def set_cookies(self, cookies: dict[str, str]) -> None:
        if not isinstance(cookies, dict):
            return
        self._session.cookies.clear()
        for key, value in cookies.items():
            if key and value is not None:
                self._set_cookie_all_domains(str(key), str(value))
        self._ensure_identity_cookies()

    def export_cookies(self) -> dict[str, str]:
        try:
            return self._session.cookies.get_dict() or {}
        except Exception:
            return {}

    def get_last_songinfo_meta(self) -> dict[str, Any]:
        return dict(self._last_songinfo_meta or {})

    def get_last_liked_meta(self) -> dict[str, Any]:
        return dict(self._last_liked_meta or {})

    def is_logged_in(self) -> bool:
        token, userid = self._login_identity()
        return bool(token and userid and userid != "0")

    def _warmup_login_requests(self) -> None:
        if not self.is_logged_in():
            return
        for url in (
            "https://www.kugou.com/",
            "https://www.kugou.com/yy/html/rank.html",
        ):
            try:
                self._session.get(url, timeout=self._timeout)
            except Exception:
                continue
        with contextlib.suppress(Exception):
            self._kugou_favor_page(page=1, pagesize=1)

    def _build_browser_cookie_items(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for name, value in self.export_cookies().items():
            cookie_name = str(name or "").strip()
            cookie_value = str(value or "").strip()
            if not cookie_name or not cookie_value:
                continue
            for domain in self._COOKIE_DOMAINS:
                items.append(
                    {
                        "name": cookie_name,
                        "value": cookie_value,
                        "domain": domain,
                        "path": "/",
                        "httpOnly": False,
                        "secure": True,
                    }
                )
        return items

    def _warmup_login_browser_context(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return

        browser = None
        context = None
        playwright = None
        try:
            playwright = sync_playwright().start()
            browser = launch_playwright_chromium(playwright, headless=True)
            context = browser.new_context(locale="zh-CN", viewport={"width": 1280, "height": 900})
            cookie_items = self._build_browser_cookie_items()
            if cookie_items:
                with contextlib.suppress(Exception):
                    context.add_cookies(cookie_items)
            page = context.new_page()
            for url in (
                "https://www.kugou.com/",
                "https://www.kugou.com/yy/html/rank.html",
            ):
                with contextlib.suppress(Exception):
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1200)
            for item in context.cookies():
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
                if name and value:
                    self._set_cookie_all_domains(name, value)
        except Exception:
            return
        finally:
            with contextlib.suppress(Exception):
                if context is not None:
                    context.close()
            with contextlib.suppress(Exception):
                if browser is not None:
                    browser.close()
            with contextlib.suppress(Exception):
                if playwright is not None:
                    playwright.stop()

    def finalize_login(self) -> dict[str, Any]:
        self._ensure_identity_cookies()
        self._warmup_login_requests()
        self._warmup_login_browser_context()
        self._warmup_login_requests()
        token, userid = self._login_identity()
        return {
            "ok": self.is_logged_in(),
            "token": token,
            "userid": userid,
            "cookies": self.export_cookies(),
            "liked_meta": self.get_last_liked_meta(),
        }

    def _build_signed_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        merged: dict[str, Any] = {
            "appid": self._APPID,
            "clientver": self._CLIENTVER,
            "clienttime": int(time.time()),
            "dfid": self._current_dfid(),
            "mid": self._current_mid(),
            "uuid": self._current_mid(),
        }
        token, userid = self._login_identity()
        if token:
            merged["token"] = token
        if userid and userid != "0":
            merged["userid"] = userid

        if params:
            for k, v in params.items():
                if v is None:
                    continue
                merged[str(k)] = v

        sign_source = "".join(sorted(f"{k}={merged[k]}" for k in merged.keys()))
        merged["signature"] = hashlib.md5(
            f"{self._WEB_SIGN_KEY}{sign_source}{self._WEB_SIGN_KEY}".encode("utf-8")
        ).hexdigest()
        return merged

    def _build_h5_signed_params(
        self,
        base_params: dict[str, Any] | None = None,
        post_data: dict[str, Any] | str | None = None,
        post_type: str = "json",
    ) -> dict[str, Any]:
        client_ts = int(time.time() * 1000)
        mid = self._current_mid()
        merged: dict[str, Any] = {
            "srcappid": str(self._SRC_APPID),
            "clientver": "20000",
            "clienttime": str(client_ts),
            "mid": mid,
            "uuid": mid,
            "dfid": self._current_dfid(),
        }
        if base_params:
            for key, value in base_params.items():
                if value is None:
                    continue
                text_key = str(key).strip()
                if not text_key:
                    continue
                merged[text_key] = str(value)

        sign_items = [f"{k}={merged[k]}" for k in sorted(merged.keys())]

        if post_data is not None:
            if isinstance(post_data, dict):
                if str(post_type or "").strip().lower() == "json":
                    import json

                    sign_items.append(json.dumps(post_data, ensure_ascii=False, separators=(",", ":")))
                else:
                    body_pairs = [f"{k}={post_data[k]}" for k in post_data]
                    sign_items.append("&".join(body_pairs))
            else:
                sign_items.append(str(post_data))

        sign_body = f"{self._WEB_SIGN_KEY}{''.join(sign_items)}{self._WEB_SIGN_KEY}"
        merged["signature"] = hashlib.md5(sign_body.encode("utf-8")).hexdigest()
        return merged

    def _build_songinfo_v2_params(
        self,
        song_hash: str | None = None,
        album_id: int | None = None,
        album_audio_id: int | None = None,
        encode_album_audio_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "appid": "1014",
            "platid": "4",
        }

        hash_text = self._normalize_hash(song_hash)
        if hash_text:
            params["hash"] = hash_text

        aid = self._safe_positive_int(album_id)
        if aid > 0:
            params["album_id"] = str(aid)
        aaid = self._safe_positive_int(album_audio_id)
        if aaid > 0:
            params["album_audio_id"] = str(aaid)

        encode_mix = str(encode_album_audio_id or "").strip()
        if encode_mix:
            params["encode_album_audio_id"] = encode_mix

        token, userid = self._login_identity()
        params["token"] = token or ""
        params["userid"] = userid or "0"
        return self._build_h5_signed_params(params)

    def _build_web_filter_headers(self, *, token: str, userid: str, clienttime: Any) -> dict[str, str]:
        return {
            "token": token,
            "userid": userid,
            "appid": str(self._APPID),
            "clienttime": str(clienttime or ""),
            "mid": self._current_mid(),
            "dfid": self._current_dfid(),
            "kg-thash": self._random_hex(16),
            "x-router": "mspace.service.kugou.com",
            "platform": "WebFilter",
            "Referer": "https://www.kugou.com/",
            "Origin": "https://www.kugou.com",
        }

    def _request_json_payload(
        self,
        method: str,
        api_url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], int]:
        request_method = str(method or "get").strip().lower()
        if request_method == "post":
            resp = self._session.post(
                api_url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=self._timeout,
            )
        else:
            resp = self._session.get(
                api_url,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
        status_code = int(resp.status_code or 0)
        resp.raise_for_status()
        raw_payload = resp.json()
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        return payload, status_code

    def _fetch_song_info(self, song_hash: str) -> dict[str, Any]:
        hash_text = self._normalize_hash(song_hash)
        if not hash_text:
            return {}
        resp = self._session.get(
            self._PLAYINFO_URL,
            params={"cmd": "playInfo", "hash": hash_text},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def _fetch_song_info_v2(
        self,
        song_hash: str | None = None,
        album_id: int | None = None,
        album_audio_id: int | None = None,
        encode_album_audio_id: str | None = None,
    ) -> dict[str, Any]:
        self._last_songinfo_meta = {
            "ok": False,
            "logged_in": self.is_logged_in(),
            "status": None,
            "err_code": None,
            "anti_brush": False,
        }
        params = self._build_songinfo_v2_params(
            song_hash=song_hash,
            album_id=album_id,
            album_audio_id=album_audio_id,
            encode_album_audio_id=encode_album_audio_id,
        )
        if "hash" not in params and "encode_album_audio_id" not in params:
            return {}

        last_payload: dict[str, Any] = {}
        for api_url in (self._PLAY_SONGINFO_V2_URL, self._PLAY_SONGINFO_V2_RETRY_URL):
            try:
                payload, _ = self._request_json_payload("get", api_url, params=params)
                if not payload:
                    continue
            except Exception as e:
                logger.debug("[KugouClient] songinfo v2 ???? url=%s: %s", api_url, e)
                continue

            data = payload.get("data")
            status = payload.get("status")
            err_code = payload.get("err_code")
            anti_code = ""
            anti_hmid = ""
            if isinstance(data, dict):
                anti_code = str(
                    data.get("SSA-CODE") or data.get("SSA_CODE") or data.get("ssa_code") or ""
                ).strip()
                anti_hmid = str(
                    data.get("SSA-HMID") or data.get("SSA_HMID") or data.get("ssa_hmid") or ""
                ).strip()
            anti_brush = bool(anti_code)
            self._last_songinfo_meta = {
                "ok": False,
                "status": status,
                "err_code": err_code,
                "anti_brush": anti_brush,
                "ssa_code": anti_code,
                "ssa_hmid": anti_hmid,
                "logged_in": self.is_logged_in(),
            }
            if isinstance(data, dict) and data:
                play_url = str(data.get("play_url") or data.get("url") or "").strip()
                if play_url:
                    self._last_songinfo_meta["ok"] = True
                    return payload
                if status == 1 and err_code in (0, None):
                    return payload
            if anti_brush or err_code not in (0, None):
                logger.info(
                    "[KugouClient] songinfo v2 miss hash=%s status=%s err_code=%s anti_brush=%s logged_in=%s",
                    str(song_hash or encode_album_audio_id or ""),
                    status,
                    err_code,
                    anti_brush,
                    self.is_logged_in(),
                )
            last_payload = payload
        return last_payload if isinstance(last_payload, dict) else {}


    @staticmethod
    def _song_url_from_info(info: dict[str, Any]) -> str:
        candidates = KugouClient._song_url_candidates_from_info(info)
        return candidates[0] if candidates else ""

    @staticmethod
    def _song_url_candidates_from_info(info: dict[str, Any]) -> list[str]:
        if not isinstance(info, dict):
            return []

        # v2 songinfo keeps playable URLs under data.play_url.
        data = info.get("data") if isinstance(info.get("data"), dict) else info
        if not isinstance(data, dict):
            data = {}

        candidates: list[str] = []
        seen: set[str] = set()

        def _add_url(raw_url: Any) -> None:
            if isinstance(raw_url, dict):
                for key in ("url", "play_url", "backup_url"):
                    value = raw_url.get(key)
                    if value:
                        _add_url(value)
                return
            text = str(raw_url or "").strip()
            if not text:
                return
            if text.startswith("//"):
                text = f"https:{text}"
            if not (text.startswith("http://") or text.startswith("https://")):
                return
            if text in seen:
                return
            seen.add(text)
            candidates.append(text)

        for key in ("play_url", "url"):
            _add_url(data.get(key))

        for key in ("play_backup_url", "backup_url"):
            backup = data.get(key)
            if isinstance(backup, list):
                for item in backup:
                    _add_url(item)
            elif isinstance(backup, str):
                _add_url(backup)
        return candidates

    @staticmethod
    def _song_url_candidates_from_payloads(*payloads: Any) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for payload in payloads:
            for candidate in KugouClient._song_url_candidates_from_info(payload):
                if candidate in seen:
                    continue
                seen.add(candidate)
                merged.append(candidate)
        return merged

    def search_song(self, keyword: str, page_num: int = 1, num_per_page: int = 20) -> list[dict[str, Any]]:
        query = str(keyword or "").strip()
        if not query:
            return []

        resp = self._session.get(
            self._SEARCH_URL,
            params={
                "keyword": query,
                "page": max(1, int(page_num or 1)),
                "pagesize": max(1, min(50, int(num_per_page or 20))),
                "platform": "WebFilter",
                "tag": "em",
                "filter": 2,
                "iscorrection": 1,
                "privilege_filter": 0,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        lists = ((data.get("data") or {}).get("lists")) or []
        songs: list[dict[str, Any]] = []
        for item in lists:
            if not isinstance(item, dict):
                continue
            song_hash = self._normalize_hash(item.get("FileHash"))
            if not song_hash:
                continue
            title = self._clean_text(item.get("SongName")) or "未知歌曲"
            artist = self._clean_text(item.get("SingerName")) or "未知歌手"
            duration_sec = self._safe_int(item.get("Duration"), 0)
            album_id = self._safe_int(item.get("AlbumID"), 0)
            audio_id = self._safe_int(item.get("Audioid"), 0)
            encode_mix = str(item.get("EMixSongID") or item.get("EncodeAlbumAudioID") or "").strip()
            duration_ms = duration_sec * 1000 if duration_sec > 0 else None
            songs.append(
                {
                    "hash": song_hash,
                    "title": title,
                    "artist": artist,
                    "duration_ms": duration_ms,
                    "album_id": album_id,
                    "audio_id": audio_id,
                    "album_audio_id": audio_id,
                    "mix_song_id": self._safe_int(item.get("MixSongID"), 0),
                    "encode_album_audio_id": encode_mix,
                    "raw": item,
                }
            )
            self._remember_song_meta(
                song_hash,
                title=title,
                artist=artist,
                duration_ms=duration_ms,
                album_id=album_id,
                album_audio_id=audio_id,
                encode_album_audio_id=encode_mix,
                raw_search=item,
            )
        return songs

    def get_song_detail(
        self,
        song_hash: str | None = None,
        album_id: int | None = None,
        album_audio_id: int | None = None,
        encode_album_audio_id: str | None = None,
    ) -> dict[str, Any] | None:
        hash_text = self._normalize_hash(song_hash)
        mix_id = str(encode_album_audio_id or "").strip()
        if not hash_text and not mix_id:
            return None

        info_v2: dict[str, Any] = {}
        info_fallback: dict[str, Any] = {}
        try:
            info_v2 = self._fetch_song_info_v2(
                song_hash=hash_text,
                album_id=album_id,
                album_audio_id=album_audio_id,
                encode_album_audio_id=mix_id,
            )
        except Exception as e:
            logger.debug("[KugouClient] songinfo v2 detail failed hash=%s: %s", hash_text or mix_id, e)

        raw_payload = info_v2 if isinstance(info_v2, dict) else {}
        raw_data = raw_payload.get("data") if isinstance(raw_payload.get("data"), dict) else {}

        title = self._clean_text(raw_data.get("song_name") or raw_data.get("audio_name"))
        artist = self._clean_text(raw_data.get("author_name"))
        duration_ms = self._safe_int(raw_data.get("timelength"), 0)
        url_candidates = self._song_url_candidates_from_payloads(raw_payload)

        if not title or not artist or duration_ms <= 0 or not url_candidates:
            try:
                info_fallback = self._fetch_song_info(hash_text)
            except Exception:
                info_fallback = {}
            if not title:
                title = self._clean_text(info_fallback.get("songName") or info_fallback.get("fileName"))
            if not artist:
                artist = self._clean_text(info_fallback.get("singerName") or info_fallback.get("author_name"))
            if duration_ms <= 0:
                duration_ms = self._safe_int(info_fallback.get("timeLength"), 0)
                if duration_ms <= 0:
                    duration_sec = self._safe_int(info_fallback.get("time"), 0)
                    duration_ms = duration_sec * 1000 if duration_sec > 0 else 0
            url_candidates = self._song_url_candidates_from_payloads(raw_payload, info_fallback)
            if not raw_payload:
                raw_payload = info_fallback

        if hash_text:
            cached_meta = self._song_meta_cache.get(hash_text, {})
            if not title:
                title = self._safe_text(cached_meta.get("title"))
            if not artist:
                artist = self._safe_text(cached_meta.get("artist"))
            if duration_ms <= 0:
                duration_ms = self._safe_positive_int(cached_meta.get("duration_ms"))

        detail = {
            "hash": hash_text,
            "title": title or hash_text or mix_id,
            "artist": artist or "????",
            "duration_ms": duration_ms if duration_ms > 0 else None,
            "album_id": self._safe_positive_int(album_id or raw_data.get("album_id")),
            "audio_id": self._safe_positive_int(
                album_audio_id or raw_data.get("album_audio_id") or raw_data.get("audio_id")
            ),
            "album_audio_id": self._safe_positive_int(
                album_audio_id or raw_data.get("album_audio_id") or raw_data.get("audio_id")
            ),
            "encode_album_audio_id": mix_id,
            "url": url_candidates[0] if url_candidates else "",
            "url_candidates": url_candidates,
            "raw": raw_payload,
        }
        self._remember_song_meta(
            hash_text,
            title=detail.get("title"),
            artist=detail.get("artist"),
            duration_ms=detail.get("duration_ms"),
            album_id=detail.get("album_id"),
            album_audio_id=detail.get("album_audio_id"),
            encode_album_audio_id=detail.get("encode_album_audio_id"),
        )
        if not detail.get("url"):
            self._apply_musicdl_detail_fallback(detail, hash_text)
        return detail

    def get_song_url(
        self,
        song_hash: str,
        album_id: int | None = None,
        album_audio_id: int | None = None,
        encode_album_audio_id: str | None = None,
    ) -> str | None:
        hash_text = self._normalize_hash(song_hash)
        mix_id = str(encode_album_audio_id or "").strip()
        if not hash_text and not mix_id:
            return None
        cached_url = self._cached_musicdl_url(hash_text, record_hit=False) if hash_text else ""

        info_v2: dict[str, Any] = {}
        try:
            info_v2 = self._fetch_song_info_v2(
                song_hash=hash_text,
                album_id=album_id,
                album_audio_id=album_audio_id,
                encode_album_audio_id=mix_id,
            )
        except Exception as e:
            logger.debug("[KugouClient] songinfo v2 URL 澶辫触 hash=%s: %s", hash_text or mix_id, e)
        url = self._song_url_from_info(info_v2)
        if url:
            return url

        if not hash_text:
            return None
        try:
            info = self._fetch_song_info(hash_text)
        except Exception as e:
            logger.debug("[KugouClient] legacy URL 澶辫触 hash=%s: %s", hash_text, e)
            info = {}
        url = self._song_url_from_info(info)
        if url:
            self._update_last_songinfo_meta(
                legacy_fallback_hit=True,
                musicdl_fallback_hit=False,
            )
            return url

        if cached_url:
            self._update_last_songinfo_meta(
                legacy_fallback_hit=False,
                musicdl_fallback_hit=True,
                musicdl_cached_hit=True,
            )
            return cached_url

        musicdl_url = self._musicdl_url_fallback(hash_text)
        if musicdl_url:
            return musicdl_url

        meta = self._update_last_songinfo_meta(
            legacy_fallback_hit=False,
            musicdl_fallback_hit=False,
        )
        if self._musicdl_disabled_reason:
            meta["musicdl_reason"] = self._musicdl_disabled_reason
        self._last_songinfo_meta = meta
        return url or None

    @staticmethod
    def _random_hex(length: int = 16) -> str:
        if length <= 0:
            length = 16
        chars = string.hexdigits.upper()
        return "".join(random.choice(chars[:16]) for _ in range(length))

    @staticmethod
    def _extract_favor_items(data: Any) -> list[dict[str, Any]]:
        """Extract liked-song list from multiple payload layouts."""
        queue: list[Any] = [data]
        seen_nodes: set[int] = set()

        while queue:
            node = queue.pop(0)
            if isinstance(node, list):
                items = [item for item in node if isinstance(item, dict)]
                if items:
                    return items
                continue
            if not isinstance(node, dict):
                continue
            node_id = id(node)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)

            for key in ("info", "songs", "song_infos", "items", "song_list", "list", "records", "rows"):
                value = node.get(key)
                if isinstance(value, list):
                    items = [item for item in value if isinstance(item, dict)]
                    if items:
                        return items

            for key in ("data", "list", "result", "page", "song_list", "items", "songs", "info"):
                value = node.get(key)
                if isinstance(value, (dict, list)):
                    queue.append(value)
        return []

    @staticmethod
    def _favor_endpoint_candidates(attempt: int, primary_url: str, retry_url: str) -> list[str]:
        candidates = [primary_url, retry_url]
        if int(attempt or 0) % 2 == 0:
            candidates.reverse()
        return candidates

    def _normalize_favor_track(self, item: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        song_hash = self._normalize_hash(
            item.get("hash")
            or item.get("audio_info_hash")
            or item.get("file_hash")
            or item.get("filehash")
            or item.get("audio_id")
        )
        if not song_hash:
            return None

        title = self._clean_text(
            item.get("songname")
            or item.get("song_name")
            or item.get("audio_name")
            or item.get("filename")
            or item.get("name")
        )
        artist = self._clean_text(
            item.get("singername")
            or item.get("singer_name")
            or item.get("author_name")
            or item.get("artist")
            or item.get("singer")
        )
        if not artist and " - " in title:
            left, right = title.split(" - ", 1)
            artist = left.strip()
            title = right.strip()

        duration_raw = (
            item.get("duration")
            or item.get("timeLength")
            or item.get("timelength")
            or item.get("timelen")
            or item.get("time_len")
            or item.get("interval")
        )
        duration_ms = self._safe_int(duration_raw, 0)
        if duration_ms > 0 and duration_ms < 1000:
            duration_ms *= 1000

        album_id = self._safe_positive_int(
            item.get("album_id") or item.get("albumid") or item.get("AlbumID")
        )
        album_audio_id = self._safe_positive_int(
            item.get("album_audio_id")
            or item.get("audio_id")
            or item.get("Audioid")
            or item.get("audioid")
        )
        encode_mix = str(
            item.get("encode_album_audio_id")
            or item.get("EMixSongID")
            or item.get("mixsongid")
            or item.get("MixSongID")
            or ""
        ).strip()
        return {
            "hash": song_hash,
            "title": title or song_hash,
            "artist": artist or "????",
            "duration_ms": duration_ms if duration_ms > 0 else None,
            "album_id": album_id,
            "audio_id": album_audio_id,
            "album_audio_id": album_audio_id,
            "encode_album_audio_id": encode_mix,
            "raw": item,
        }

    def _build_favor_request_profiles(
        self,
        *,
        token: str,
        userid: str,
        page: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        page_value = max(1, int(page or 1))
        size_value = max(1, min(200, int(page_size or 100)))
        filter_fields = "hash,filename,singername,songname,duration,album_id,album_audio_id,mixsongid"

        now_ts = int(time.time())
        legacy_params = self._build_signed_params(
            {
                "appid": self._APPID,
                "clientver": 11209,
                "clienttime": now_ts,
                "token": token,
                "userid": userid,
                "area_code": 1,
                "pagesize": size_value,
                "page": page_value,
                "filter_fields": filter_fields,
            }
        )
        legacy_headers = self._build_web_filter_headers(
            token=token,
            userid=userid,
            clienttime=now_ts,
        )

        h5_base = {
            "appid": str(self._APPID),
            "token": token,
            "userid": userid,
            "area_code": "1",
            "pagesize": str(size_value),
            "page": str(page_value),
            "filter_fields": filter_fields,
        }
        h5_get_params = self._build_h5_signed_params(h5_base)
        h5_get_headers = self._build_web_filter_headers(
            token=token,
            userid=userid,
            clienttime=h5_get_params.get("clienttime"),
        )

        h5_body = {
            "area_code": 1,
            "pagesize": size_value,
            "page": page_value,
            "filter_fields": filter_fields,
        }
        h5_post_params = self._build_h5_signed_params(
            {
                "appid": str(self._APPID),
                "token": token,
                "userid": userid,
            },
            post_data=h5_body,
            post_type="json",
        )
        h5_post_headers = dict(h5_get_headers)
        h5_post_headers["Content-Type"] = "application/json;charset=UTF-8"

        return [
            {
                "name": "legacy_get",
                "method": "get",
                "params": legacy_params,
                "headers": legacy_headers,
            },
            {
                "name": "h5_get",
                "method": "get",
                "params": h5_get_params,
                "headers": h5_get_headers,
            },
            {
                "name": "h5_post_json",
                "method": "post",
                "params": h5_post_params,
                "headers": h5_post_headers,
                "json": h5_body,
            },
        ]

    def _kugou_favor_page(self, page: int = 1, pagesize: int = 100) -> list[dict[str, Any]]:
        token, userid = self._login_identity()
        if not token or not userid or userid == "0":
            self._last_liked_meta = {
                "ok": False,
                "reason": "no_login_token",
            }
            return []

        page_size = max(1, min(200, int(pagesize or 100)))
        page_value = max(1, int(page or 1))
        payload: dict[str, Any] = {}
        request_error = ""
        request_profile = ""
        request_url = ""
        request_status: int | None = None
        request_attempt = 0
        max_attempts = max(1, int(self._FAVOR_RETRY_TIMES or 1))
        retry_backoff = max(0.0, float(self._FAVOR_RETRY_BACKOFF or 0.0))
        for attempt in range(1, max_attempts + 1):
            request_attempt = attempt
            profiles = self._build_favor_request_profiles(
                token=token,
                userid=userid,
                page=page_value,
                page_size=page_size,
            )
            endpoint_candidates = self._favor_endpoint_candidates(
                attempt,
                self._FAVOR_SONG_LIST_URL,
                self._FAVOR_SONG_LIST_RETRY_URL,
            )

            for profile in profiles:
                profile_name = str(profile.get("name") or "unknown")
                method = str(profile.get("method") or "get").strip().lower()
                params = profile.get("params") if isinstance(profile.get("params"), dict) else {}
                headers = profile.get("headers") if isinstance(profile.get("headers"), dict) else {}
                json_body = profile.get("json") if isinstance(profile.get("json"), dict) else None
                for api_url in endpoint_candidates:
                    request_profile = profile_name
                    request_url = api_url
                    try:
                        payload, request_status = self._request_json_payload(
                            method,
                            api_url,
                            params=params,
                            headers=headers,
                            json_body=json_body,
                        )
                        if payload:
                            break
                        request_error = "empty_payload"
                    except requests.HTTPError as e:
                        status = e.response.status_code if e.response is not None else request_status
                        request_status = int(status or 0) if status is not None else request_status
                        request_error = str(e)
                        should_retry = request_status in (408, 425, 429) or request_status >= 500
                        if should_retry and attempt < max_attempts:
                            logger.warning(
                                "[KugouClient] favor list retry profile=%s url=%s status=%s attempt=%d/%d",
                                profile_name,
                                api_url,
                                request_status,
                                attempt,
                                max_attempts,
                            )
                        else:
                            logger.debug(
                                "[KugouClient] favor list request failed profile=%s url=%s status=%s err=%s",
                                profile_name,
                                api_url,
                                request_status,
                                e,
                            )
                        continue
                    except (requests.Timeout, requests.ConnectionError) as e:
                        request_error = str(e)
                        if attempt < max_attempts:
                            logger.warning(
                                "[KugouClient] favor list network retry profile=%s url=%s attempt=%d/%d err=%s",
                                profile_name,
                                api_url,
                                attempt,
                                max_attempts,
                                e,
                            )
                        else:
                            logger.debug(
                                "[KugouClient] favor list request failed profile=%s url=%s err=%s",
                                profile_name,
                                api_url,
                                e,
                            )
                        continue
                    except Exception as e:
                        request_error = str(e)
                        logger.debug(
                            "[KugouClient] favor list request failed profile=%s url=%s err=%s",
                            profile_name,
                            api_url,
                            e,
                        )
                        continue
                if payload:
                    break
            if payload:
                break
            if attempt < max_attempts and retry_backoff > 0:
                time.sleep(retry_backoff * attempt)
        if not payload:
            self._last_liked_meta = {
                "ok": False,
                "reason": "request_failed",
                "error": request_error,
                "request_profile": request_profile or "none",
                "request_url": request_url or "none",
                "status_code": request_status,
                "attempt": request_attempt,
            }
            return []

        status = payload.get("status")
        err_code = payload.get("err_code")
        if err_code is None:
            err_code = payload.get("error_code")
        message = str(payload.get("error_msg") or payload.get("msg") or payload.get("message") or "").strip()
        data = payload.get("data")
        items = self._extract_favor_items(data if data is not None else payload)
        if not items:
            self._last_liked_meta = {
                "ok": False,
                "reason": "items_not_list",
                "status": status,
                "err_code": err_code,
                "request_profile": request_profile or "none",
                "request_url": request_url or "none",
                "status_code": request_status,
                "attempt": request_attempt,
                "message": message,
            }
            return []
        out: list[dict[str, Any]] = []
        for item in items:
            song = self._normalize_favor_track(item)
            if not song:
                continue
            out.append(song)
        self._last_liked_meta = {
            "ok": bool(out),
            "reason": "ok" if out else "items_empty",
            "status": status,
            "err_code": err_code,
            "message": message,
            "request_profile": request_profile or "none",
            "request_url": request_url or "none",
            "status_code": request_status,
            "attempt": request_attempt,
            "count": len(out),
            "page": int(page or 1),
        }
        return out

    def get_liked_tracks(self, limit: int = 32) -> list[dict[str, Any]]:
        self._last_liked_meta = {}
        max_items = max(1, int(limit or 32))
        if not self.is_logged_in():
            self._last_liked_meta = {
                "ok": False,
                "reason": "not_logged_in",
            }
            return []

        songs: list[dict[str, Any]] = []
        seen_hash: set[str] = set()
        page = 1
        page_size = min(100, max_items)
        while len(songs) < max_items and page <= 10:
            try:
                page_items = self._kugou_favor_page(page=page, pagesize=page_size)
            except Exception as e:
                logger.debug("[KugouClient] liked tracks fetch failed page=%s: %s", page, e)
                self._last_liked_meta = {
                    "ok": False,
                    "reason": "page_exception",
                    "page": page,
                    "error": str(e),
                }
                break
            if not page_items:
                break
            for song in page_items:
                song_hash = str(song.get("hash") or "").strip().upper()
                if not song_hash or song_hash in seen_hash:
                    continue
                seen_hash.add(song_hash)
                songs.append(song)
                if len(songs) >= max_items:
                    break
            if len(page_items) < page_size:
                break
            page += 1
        if songs:
            self._last_liked_meta = {
                "ok": True,
                "reason": "ok",
                "count": len(songs),
            }
        elif not self._last_liked_meta:
            self._last_liked_meta = {
                "ok": False,
                "reason": "empty",
            }
        return songs[:max_items]

    def create_login_qr(self, qr_appid: int | None = None) -> dict[str, Any]:
        appid = int(qr_appid or self._QR_APPID)
        params = self._build_signed_params(
            {
                "appid": appid,
                "type": 1,
                "plat": 4,
                "srcappid": self._SRC_APPID,
                "qrcode_txt": f"https://h5.kugou.com/apps/loginQRCode/html/index.html?appid={self._APPID}&",
            }
        )
        resp = self._session.get(self._QR_KEY_URL, params=params, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            data = {}

        key = str(data.get("qrcode") or "").strip()
        img_data = str(data.get("qrcode_img") or "").strip()
        qr_png: bytes | None = None
        if img_data.startswith("data:image") and "," in img_data:
            b64 = img_data.split(",", 1)[1]
            try:
                qr_png = base64.b64decode(b64)
            except Exception:
                qr_png = None

        return {
            "key": key,
            "qr_url": f"https://h5.kugou.com/apps/loginQRCode/html/index.html?qrcode={key}" if key else "",
            "qr_png": qr_png,
            "raw": payload if isinstance(payload, dict) else {},
        }

    def poll_login_qr(self, qr_key: str) -> dict[str, Any]:
        key = str(qr_key or "").strip()
        if not key:
            return {"status": -1, "message": "浜岀淮鐮?key 涓虹┖"}
        params = self._build_signed_params(
            {
                "appid": self._APPID,
                "plat": 4,
                "srcappid": self._SRC_APPID,
                "qrcode": key,
            }
        )
        resp = self._session.get(self._QR_CHECK_URL, params=params, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            data = {}
        status = self._safe_int(data.get("status"), -1)
        token = str(data.get("token") or "").strip()
        userid = str(data.get("userid") or "").strip()
        nickname = self._clean_text(data.get("nickname") or data.get("username"))
        message = str((payload or {}).get("error_msg") or "").strip()

        return {
            "status": status,
            "token": token,
            "userid": userid,
            "nickname": nickname,
            "message": message,
            "raw": payload if isinstance(payload, dict) else {},
        }

    def set_login_token(self, token: str, userid: str | int) -> bool:
        token_text = str(token or "").strip()
        uid_text = str(userid or "").strip()
        if not token_text or not uid_text or uid_text == "0":
            return False
        try:
            self._set_cookie_all_domains("token", token_text)
            self._set_cookie_all_domains("userid", uid_text)

            ku = self._kugou_cookie_payload()
            ku["t"] = token_text
            ku["KugooID"] = uid_text
            if "a_id" not in ku:
                ku["a_id"] = str(self._APPID)
            ku_cookie = "&".join(f"{k}={ku[k]}" for k in ku if str(k).strip())
            if ku_cookie:
                self._set_cookie_all_domains("KuGoo", ku_cookie)

            self._ensure_identity_cookies()
            return True
        except Exception:
            return False


_instance: KugouClient | None = None


def get_kugou_client() -> KugouClient:
    global _instance
    if _instance is None:
        _instance = KugouClient()
    return _instance


