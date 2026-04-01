"""QQ Music client based on stable web endpoints plus musicdl fallback."""
from __future__ import annotations

import contextlib
import io
import json
import random
import re
import time
from typing import Any

import requests

from lib.core.logger import get_logger

logger = get_logger(__name__)


class QQmisic:
    _API_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
    _PLAYLIST_LIST_URL = "https://c.y.qq.com/rsc/fcgi-bin/fcg_user_created_diss"
    _PLAYLIST_DETAIL_URL = "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
    _PLAYLIST_DETAIL_LEGACY_URL = "https://i.y.qq.com/qzone-music/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
    _PROFILE_HOMEPAGE_URL = "https://c.y.qq.com/rsc/fcgi-bin/fcg_get_profile_homepage.fcg"
    _PROFILE_HOMEPAGE_C6_URL = "https://c6.y.qq.com/rsc/fcgi-bin/fcg_get_profile_homepage.fcg"
    _PROFILE_ORDER_ASSET_URL = "https://c.y.qq.com/fav/fcgi-bin/fcg_get_profile_order_asset.fcg"
    _MYFAV_MAP_URL = "https://c.y.qq.com/splcloud/fcgi-bin/fcg_musiclist_getmyfav.fcg"
    _LIKED_DIRID = 201
    _LIKED_NAME_HINTS = ("我喜欢的音乐", "喜欢的音乐", "我喜欢", "喜欢", "默认收藏", "收藏", "favorite", "fav", "myfav")
    _LIKED_DIRID_CANDIDATES = (201, 1)

    def __init__(self, timeout: tuple[float, float] = (8.0, 20.0)) -> None:
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "Referer": "https://y.qq.com/",
                "Origin": "https://y.qq.com",
            }
        )
        self._song_cache: dict[str, dict[str, Any]] = {}
        self._musicdl_client = None
        self._musicdl_init_done = False
        self._last_vkey_meta: dict[str, Any] = {}
        self._last_liked_meta: dict[str, Any] = {}

    @staticmethod
    def _safe_int(raw: Any, default: int = 0) -> int:
        try:
            return int(raw)
        except Exception:
            return default

    @staticmethod
    def _repair_mojibake(raw: Any) -> str:
        text = str(raw or "")
        if not text:
            return ""
        if not any(128 <= ord(char) <= 255 for char in text):
            return text
        try:
            repaired = text.encode("latin1").decode("utf-8")
        except Exception:
            return text
        cjk_count = sum(1 for char in repaired if "一" <= char <= "鿿")
        return repaired if cjk_count > 0 else text

    @classmethod
    def _clean_text(cls, raw: Any) -> str:
        return re.sub(r"\s+", " ", cls._repair_mojibake(raw).strip())

    @staticmethod
    def _hash33(text: str) -> int:
        value = 5381
        for char in str(text or ""):
            value += (value << 5) + ord(char)
        return value & 0x7FFFFFFF

    @staticmethod
    def _normalize_uin(raw_uin: str | None) -> str:
        text = str(raw_uin or "0").strip()
        match = re.search(r"(\d+)", text)
        return match.group(1) if match else "0"

    def _cookie_text(self, *names: str) -> str:
        cookies = self.export_cookies()
        for name in names:
            value = str(cookies.get(name) or "").strip()
            if value:
                return value
        return ""

    def _uin_from_cookies(self) -> str:
        return self._normalize_uin(self._cookie_text("uin", "qqmusic_uin", "p_uin", "wxuin", "qm_uin"))

    def _has_auth_cookie(self) -> bool:
        return bool(self._cookie_text("p_skey", "skey", "qqmusic_key", "music_key", "qm_keyst", "pt4_token"))

    def _has_music_auth_cookie(self) -> bool:
        return bool(self._cookie_text("qqmusic_key", "music_key", "qm_keyst"))

    def _g_tk(self, *, use_new: bool = False) -> int:
        key = self._cookie_text("qqmusic_key", "p_skey", "skey") if use_new else self._cookie_text("skey", "qqmusic_key")
        return self._hash33(key)

    def _default_comm(self) -> dict[str, Any]:
        uin = self._uin_from_cookies()
        return {
            "cv": 0,
            "ct": 24,
            "format": "json",
            "inCharset": "utf-8",
            "outCharset": "utf-8",
            "notice": 0,
            "platform": "yqq.json",
            "needNewCode": 1,
            "uin": uin,
            "g_tk": self._g_tk(use_new=False),
            "g_tk_new_20200303": self._g_tk(use_new=True),
        }

    def get_session(self) -> requests.Session:
        return self._session

    def set_cookies(self, cookies: dict[str, str]) -> None:
        merged = {str(k or "").strip(): str(v or "").strip() for k, v in (cookies or {}).items() if str(k or "").strip()}
        if merged.get("p_uin") and not merged.get("uin"):
            merged["uin"] = merged["p_uin"]
        if merged.get("p_skey") and not merged.get("skey"):
            merged["skey"] = merged["p_skey"]
        self._session.cookies.clear()
        for key, value in merged.items():
            if not key:
                continue
            self._session.cookies.set(key, value)
            for domain in (".qq.com", ".y.qq.com", ".music.qq.com", ".c.y.qq.com", ".c6.y.qq.com"):
                self._session.cookies.set(key, value, domain=domain)

    def export_cookies(self) -> dict[str, str]:
        return self._session.cookies.get_dict() or {}

    def is_logged_in(self) -> bool:
        return self._uin_from_cookies() != "0" and self._has_auth_cookie()

    def get_last_vkey_meta(self) -> dict[str, Any]:
        return dict(self._last_vkey_meta)

    def get_last_liked_meta(self) -> dict[str, Any]:
        return dict(self._last_liked_meta)

    def _post_musicu(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._session.post(self._API_URL, json=payload, timeout=self._timeout)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _request_json_text_payload(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        request_headers = dict(self._session.headers)
        if headers:
            request_headers.update(headers)
        response = self._session.get(url, params=params, headers=request_headers, timeout=self._timeout)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        if not response.encoding or response.encoding.lower() in {"iso-8859-1", "latin1", "latin-1"}:
            response.encoding = "utf-8"
        text = response.text.strip()
        if not text:
            return {}, response.status_code
        try:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}, response.status_code
        except Exception:
            pass
        start = text.find("(")
        end = text.rfind(")")
        if start >= 0 and end > start:
            text = text[start + 1 : end]
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}, response.status_code


    def _profile_headers(self, target_uin: str | None = None) -> dict[str, str]:
        normalized_uin = self._normalize_uin(str(target_uin or self._uin_from_cookies() or "0"))
        referer = f"https://y.qq.com/n/ryqq/profile/{normalized_uin}" if normalized_uin != "0" else "https://y.qq.com/portal/profile.html"
        return {"Referer": referer, "Origin": "https://y.qq.com"}

    def _playlist_headers(self, playlist_id: int | None = None) -> dict[str, str]:
        referer = f"https://y.qq.com/n/yqq/playlist/{int(playlist_id)}.html" if playlist_id and int(playlist_id) > 0 else "https://y.qq.com/n/yqq/playlist/1.html"
        return {"Referer": referer, "Origin": "https://y.qq.com"}

    def _request_profile_homepage_detail(self, target_uin: str | int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        normalized_uin = self._normalize_uin(str(target_uin or self._uin_from_cookies() or "0"))
        if normalized_uin == "0":
            return {}, {"ok": False, "reason": "no_uin"}

        tried: list[dict[str, Any]] = []
        param_variants = (
            {"format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0, "cid": 205360838, "ct": 24, "userid": normalized_uin, "reqfrom": 1, "reqtype": 0, "hostUin": 0, "loginUin": normalized_uin},
            {"format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0, "cid": 205360838, "ct": 24, "userid": normalized_uin, "reqfrom": 1, "reqtype": 0, "hostUin": normalized_uin, "loginUin": normalized_uin, "uin": normalized_uin, "g_tk": self._g_tk(use_new=False), "g_tk_new_20200303": self._g_tk(use_new=True)},
            {"format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0, "cid": 205360838, "ct": 24, "userid": 0, "reqfrom": 1, "reqtype": 0, "hostUin": 0, "loginUin": normalized_uin},
        )
        for url in (self._PROFILE_HOMEPAGE_C6_URL, self._PROFILE_HOMEPAGE_URL):
            for params in param_variants:
                try:
                    payload, status = self._request_json_text_payload(url, params=params, headers=self._profile_headers(normalized_uin))
                except Exception as exc:
                    tried.append({"url": url, "userid": params.get("userid"), "error": str(exc)})
                    continue
                detail = (payload.get("data") if isinstance(payload.get("data"), dict) else payload) if isinstance(payload, dict) else {}
                if isinstance(detail, dict) and detail:
                    return detail, {"ok": True, "source": url, "status": status, "userid": params.get("userid")}
                tried.append({"url": url, "userid": params.get("userid"), "status": status, "empty": True})
        return {}, {"ok": False, "reason": "profile_homepage_empty", "uin": normalized_uin, "tried": tried}

    def _extract_profile_mymusic_playlist(self, detail: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(detail, dict):
            return None
        mymusic = detail.get("mymusic")
        if not isinstance(mymusic, list) or not mymusic:
            return None
        first = mymusic[0] if isinstance(mymusic[0], dict) else {}
        disstid = self._safe_int(first.get("id") or first.get("tid") or first.get("dissid") or first.get("disstid"), 0)
        if disstid <= 0:
            return None
        return {
            "disstid": disstid,
            "dirid": self._LIKED_DIRID,
            "name": self._clean_text(first.get("title") or first.get("name") or "我喜欢的音乐") or "我喜欢的音乐",
            "song_count": self._safe_int(first.get("num") or first.get("songnum") or first.get("song_count"), 0),
            "raw": first,
        }

    def _fetch_myfav_map(self, uin: str | int | None = None) -> tuple[int, dict[str, Any]]:
        target_uin = self._normalize_uin(str(uin or self._uin_from_cookies() or "0"))
        if target_uin == "0":
            return 0, {"ok": False, "reason": "no_uin"}

        tried: list[dict[str, Any]] = []
        params_variants = (
            {"g_tk": 5381, "uin": target_uin, "format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0},
            {"g_tk": self._g_tk(use_new=False), "uin": target_uin, "format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0},
        )
        for params in params_variants:
            try:
                payload, status = self._request_json_text_payload(self._MYFAV_MAP_URL, params=params, headers={"Referer": "https://y.qq.com/portal/profile.html", "Origin": "https://y.qq.com"})
            except Exception as exc:
                tried.append({"g_tk": params.get("g_tk"), "error": str(exc)})
                continue
            container = (payload.get("data") if isinstance(payload.get("data"), dict) else payload) if isinstance(payload, dict) else {}
            playlist_id = self._safe_int(container.get("map") or container.get("id") or container.get("dissid") or container.get("disstid"), 0)
            playlist_mid = self._clean_text(container.get("mapmid") or container.get("dissmid") or container.get("mid"))
            if playlist_id > 0:
                return playlist_id, {"ok": True, "reason": "myfav_map", "uin": target_uin, "playlist_id": playlist_id, "playlist_mid": playlist_mid, "status": status}
            tried.append({"g_tk": params.get("g_tk"), "status": status, "playlist_id": playlist_id})
        return 0, {"ok": False, "reason": "myfav_map_missing", "uin": target_uin, "tried": tried}

    def _extract_artist(self, song: dict[str, Any]) -> str:
        for key in ("singer", "singers", "artist", "artists"):
            value = song.get(key)
            if isinstance(value, list) and value:
                first = value[0]
                text = self._clean_text(first.get("name") or first.get("title")) if isinstance(first, dict) else self._clean_text(first)
                if text:
                    return text.split("/", 1)[0].strip()
            elif isinstance(value, dict):
                text = self._clean_text(value.get("name") or value.get("title"))
                if text:
                    return text
            else:
                text = self._clean_text(value)
                if text:
                    return text.split("/", 1)[0].strip()
        return "Unknown Artist"

    def _normalize_song(self, song: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(song, dict):
            return None
        mid = self._clean_text(song.get("mid") or song.get("songmid") or song.get("songMid"))
        if not mid:
            return None
        file_info = song.get("file") if isinstance(song.get("file"), dict) else {}
        interval = self._safe_int(song.get("interval") or song.get("duration"), 0)
        normalized = {
            "id": self._safe_int(song.get("id") or song.get("songid"), 0),
            "mid": mid,
            "media_mid": self._clean_text(file_info.get("media_mid") or song.get("media_mid") or mid) or mid,
            "title": self._clean_text(song.get("title") or song.get("name")) or mid,
            "artist": self._extract_artist(song),
            "duration_ms": interval * 1000 if 0 < interval < 100000 else interval or None,
            "raw": song,
        }
        self._song_cache[mid] = dict(normalized)
        return normalized

    def _get_musicdl_client(self):
        if self._musicdl_init_done:
            return self._musicdl_client
        self._musicdl_init_done = True
        try:
            from musicdl.modules.sources.qq import QQMusicClient as MusicDLQQMusicClient
            self._musicdl_client = MusicDLQQMusicClient(disable_print=True, strict_limit_search_size_per_page=True, search_size_per_page=10, search_size_per_source=20)
        except Exception as exc:
            logger.warning("[QQMusic] init musicdl failed: %s", exc)
            self._musicdl_client = None
        return self._musicdl_client

    def _musicdl_search(self, keyword: str) -> list[dict[str, Any]]:
        client = self._get_musicdl_client()
        if client is None:
            return []
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                results = client.search(keyword, num_threadings=1)
        except Exception as exc:
            logger.debug("[QQMusic] musicdl search failed keyword=%s: %s", keyword, exc)
            return []
        out: list[dict[str, Any]] = []
        for item in results if isinstance(results, list) else []:
            mid = self._clean_text(item.get("identifier"))
            if not mid:
                continue
            normalized = {
                "id": 0,
                "mid": mid,
                "media_mid": mid,
                "title": self._clean_text(item.get("song_name")) or mid,
                "artist": self._clean_text(item.get("singers")) or "Unknown Artist",
                "duration_ms": None,
                "url": self._clean_text(item.get("download_url")),
                "raw": item,
            }
            self._song_cache[mid] = dict(normalized)
            out.append(normalized)
        return out

    def _search_score(self, query: str, song: dict[str, Any]) -> tuple[int, int, int, int]:
        query_text = query.lower().strip()
        tokens = [token for token in re.split(r"\s+", query_text) if token]
        title = str(song.get("title") or "").strip().lower()
        artist = str(song.get("artist") or "").strip().lower()
        title_hits = sum(1 for token in tokens if token in title)
        artist_hits = sum(1 for token in tokens if token in artist)
        exact = int(query_text == title)
        full_hit = int(query_text in title) * 4 + int(query_text in artist)
        hit = title_hits * 3 + artist_hits + full_hit
        penalty = 0 if hit else 1
        return penalty, -exact, -title_hits, -hit

    def search_song(self, keyword: str, page_num: int = 1, num_per_page: int = 20) -> list[dict[str, Any]]:
        query = self._clean_text(keyword)
        if not query:
            return []
        songs: list[dict[str, Any]] = []
        payload = {
            "comm": {"ct": 19, "cv": 1859, "uin": self._uin_from_cookies()},
            "req": {
                "module": "music.search.SearchCgiService",
                "method": "DoSearchForQQMusicDesktop",
                "param": {
                    "grp": 1,
                    "num_per_page": max(1, min(50, int(num_per_page or 20))),
                    "page_num": max(1, int(page_num or 1)),
                    "query": query,
                    "search_type": 0,
                },
            },
        }
        try:
            data = self._post_musicu(payload)
            request_block = data.get("req") if isinstance(data.get("req"), dict) else {}
            body = ((request_block.get("data") or {}).get("body") or {}) if request_block else {}
            song_block = body.get("song") if isinstance(body.get("song"), dict) else {}
            items = song_block.get("list") or body.get("item_song") or []
            for item in items if isinstance(items, list) else []:
                normalized = self._normalize_song(item)
                if normalized:
                    songs.append(normalized)
        except Exception as exc:
            logger.warning("[QQMusic] search request failed keyword=%s: %s", query, exc)
        if not songs:
            songs = self._musicdl_search(query)
        ordered = sorted(songs, key=lambda item: self._search_score(query, item))
        return [dict(item) for item in ordered[: max(1, int(num_per_page or 20))]]

    def get_song_detail(self, song_mid: str | None = None) -> dict[str, Any] | None:
        mid = self._clean_text(song_mid)
        if mid.startswith("qq:"):
            parts = mid.split(":")
            mid = parts[1].strip() if len(parts) >= 2 else mid
        if not mid:
            return None
        cached = self._song_cache.get(mid)
        if cached:
            return dict(cached)
        try:
            data = self._post_musicu(
                {
                    "comm": self._default_comm(),
                    "req_0": {
                        "module": "music.pf_song_detail_svr",
                        "method": "get_song_detail_yqq",
                        "param": {"song_mid": mid, "song_type": 0},
                    },
                }
            )
            request_block = data.get("req_0") if isinstance(data.get("req_0"), dict) else {}
            request_data = request_block.get("data") if isinstance(request_block.get("data"), dict) else request_block
            track = request_data.get("track_info") or request_data.get("songinfo") or {}
            normalized = self._normalize_song(track) if isinstance(track, dict) else None
            if normalized:
                return dict(normalized)
        except Exception as exc:
            logger.debug("[QQMusic] detail request failed mid=%s: %s", mid, exc)
        fallback = self._musicdl_search(mid)
        return dict(fallback[0]) if fallback else None

    def _filename_ladder(self, song_mid: str, media_mid: str) -> list[str]:
        song_text = self._clean_text(song_mid)
        media_text = self._clean_text(media_mid)
        candidates = [
            f"M500{song_text}{song_text}.mp3" if song_text else "",
            f"M800{song_text}{song_text}.mp3" if song_text else "",
            f"C400{song_text}{song_text}.m4a" if song_text else "",
            f"M500{media_text}.mp3" if media_text else "",
            f"M800{media_text}.mp3" if media_text else "",
            f"C400{media_text}.m4a" if media_text else "",
        ]
        ordered: list[str] = []
        for item in candidates:
            value = self._clean_text(item)
            if value and value not in ordered:
                ordered.append(value)
        return ordered

    def _request_vkey_url(self, song_mid: str, media_mid: str) -> str:
        uin = self._uin_from_cookies()
        filenames = self._filename_ladder(song_mid, media_mid)
        if not filenames:
            return ""
        payload = {
            "comm": {"uin": uin, "format": "json", "ct": 24, "cv": 0},
            "req_0": {
                "module": "vkey.GetVkeyServer",
                "method": "CgiGetVkey",
                "param": {
                    "guid": "10000",
                    "songmid": [song_mid],
                    "songtype": [0],
                    "uin": uin,
                    "loginflag": 1,
                    "platform": "20",
                    "filename": [filenames[0]],
                },
            },
        }
        data = self._post_musicu(payload)
        request_block = data.get("req_0") if isinstance(data.get("req_0"), dict) else {}
        request_data = request_block.get("data") if isinstance(request_block.get("data"), dict) else request_block
        info_list = request_data.get("midurlinfo") or request_data.get("midUrlInfo") or []
        if not isinstance(info_list, list) or not info_list:
            return ""
        info = info_list[0] if isinstance(info_list[0], dict) else {}
        purl = self._clean_text(info.get("purl") or info.get("wifiurl") or info.get("url"))
        if not purl:
            return ""
        sip_list = request_data.get("sip") or request_data.get("sipList") or []
        base_url = self._clean_text(sip_list[0]) if isinstance(sip_list, list) and sip_list else ""
        return (base_url.rstrip("/") + "/" + purl.lstrip("/")) if base_url else (purl if purl.startswith("http") else "https://isure.stream.qqmusic.qq.com/" + purl.lstrip("/"))

    def get_song_url(self, song_mid: str, media_mid: str | None = None) -> str | None:
        self._last_vkey_meta = {}
        raw_mid = self._clean_text(song_mid)
        mid = raw_mid
        if raw_mid.startswith("qq:"):
            parts = raw_mid.split(":")
            if len(parts) >= 2:
                mid = parts[1].strip()
            if len(parts) >= 3 and not media_mid:
                media_mid = parts[2].strip() or None
        detail = self.get_song_detail(mid) or {}
        media_text = self._clean_text(media_mid or detail.get("media_mid") or mid) or mid
        url = self._request_vkey_url(mid, media_text)
        if url:
            self._last_vkey_meta = {"ok": True, "source": "musicu_vkey", "song_mid": mid, "media_mid": media_text, "logged_in": self.is_logged_in()}
            return url
        self._last_vkey_meta = {"ok": False, "source": "request_failed", "song_mid": mid, "media_mid": media_text, "logged_in": self.is_logged_in()}
        return None

    def _normalize_playlist_summary(self, item: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        dirid = self._safe_int(item.get("dirid"), 0)
        disstid = self._safe_int(item.get("tid") or item.get("dissid") or item.get("disstid") or item.get("id") or dirid, 0)
        if disstid <= 0:
            return None
        return {"disstid": disstid, "dirid": dirid, "name": self._clean_text(item.get("diss_name") or item.get("dissname") or item.get("name")) or str(disstid), "song_count": self._safe_int(item.get("song_cnt") or item.get("songnum") or item.get("song_count"), 0), "raw": item}

    def get_user_playlists(self, uin: str | int | None = None, size: int = 128) -> list[dict[str, Any]]:
        target_uin = self._normalize_uin(str(uin or self._uin_from_cookies() or "0"))
        if target_uin == "0":
            return []

        playlists: list[dict[str, Any]] = []
        seen: set[int] = set()

        def _append_items(raw_items: Any) -> None:
            if not isinstance(raw_items, list):
                return
            for item in raw_items:
                normalized = self._normalize_playlist_summary(item)
                if not normalized:
                    continue
                disstid = self._safe_int(normalized.get("disstid"), 0)
                if disstid <= 0 or disstid in seen:
                    continue
                seen.add(disstid)
                playlists.append(normalized)

        size_value = max(1, min(512, int(size or 128)))
        created_variants = (
            ({"hostUin": 0, "hostuin": target_uin, "sin": 0, "size": size_value, "r": random.random(), "g_tk": 5381, "loginUin": 0, "format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0}, {"Referer": "https://y.qq.com/portal/profile.html", "Origin": "https://y.qq.com"}),
            ({"format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0, "hostuin": target_uin, "sin": 0, "size": size_value, "rnd": random.random()}, {"Referer": f"https://y.qq.com/portal/profile.html?uin={target_uin}", "Origin": "https://y.qq.com"}),
            ({"format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0, "hostuin": target_uin, "hostUin": target_uin, "loginUin": target_uin, "uin": target_uin, "sin": 0, "size": size_value, "ct": 20, "cv": 0, "rnd": random.random(), "g_tk": self._g_tk(use_new=False), "g_tk_new_20200303": self._g_tk(use_new=True)}, self._profile_headers(target_uin)),
        )
        for params, headers in created_variants:
            try:
                payload, _status = self._request_json_text_payload(self._PLAYLIST_LIST_URL, params=params, headers=headers)
            except Exception:
                continue
            if isinstance(payload, dict):
                _append_items(payload.get("disslist") or payload.get("list") or ((payload.get("data") or {}).get("disslist") if isinstance(payload.get("data"), dict) else []))

        detail, _detail_meta = self._request_profile_homepage_detail(target_uin)
        mymusic_playlist = self._extract_profile_mymusic_playlist(detail)
        if mymusic_playlist is not None and self._safe_int(mymusic_playlist.get("disstid"), 0) not in seen:
            seen.add(self._safe_int(mymusic_playlist.get("disstid"), 0))
            playlists.append(mymusic_playlist)

        favorite_params = {
            "ct": 20,
            "cid": 205360956,
            "userid": target_uin,
            "reqtype": 3,
            "sin": 0,
            "ein": max(20, size_value),
            "format": "json",
            "inCharset": "utf8",
            "outCharset": "utf-8",
            "notice": 0,
            "platform": "yqq.json",
            "needNewCode": 0,
        }
        try:
            payload, _status = self._request_json_text_payload(
                self._PROFILE_ORDER_ASSET_URL,
                params=favorite_params,
                headers=self._profile_headers(target_uin),
            )
            if isinstance(payload, dict):
                favorite_items = ((payload.get("data") or {}).get("cdlist") if isinstance(payload.get("data"), dict) else None) or payload.get("cdlist") or []
                _append_items(favorite_items)
        except Exception:
            pass

        playlists.sort(key=lambda row: self._playlist_score(row)[0], reverse=True)
        return playlists

    def _resolve_myfav_dissid(self, uin: str | int | None = None) -> tuple[int, dict[str, Any]]:
        target_uin = self._normalize_uin(str(uin or self._uin_from_cookies() or "0"))
        if target_uin == "0":
            return 0, {"ok": False, "reason": "no_uin"}

        map_playlist_id, map_meta = self._fetch_myfav_map(target_uin)
        if map_playlist_id > 0:
            return map_playlist_id, map_meta

        detail, detail_meta = self._request_profile_homepage_detail(target_uin)
        mymusic_playlist = self._extract_profile_mymusic_playlist(detail)
        if mymusic_playlist is not None:
            playlist_id = self._safe_int(mymusic_playlist.get("disstid"), 0)
            if playlist_id > 0:
                return playlist_id, {"ok": True, "reason": "profile_mymusic", "uin": target_uin, "playlist_id": playlist_id, "detail_meta": detail_meta}

        playlists = self.get_user_playlists(uin=target_uin, size=256)
        for item in playlists:
            dirid = self._safe_int(item.get("dirid"), 0)
            disstid = self._safe_int(item.get("disstid"), 0)
            name = self._clean_text(item.get("name")).lower()
            if disstid > 0 and (dirid in self._LIKED_DIRID_CANDIDATES or name in {"我喜欢的音乐", "喜欢的音乐"} or any(hint in name for hint in self._LIKED_NAME_HINTS)):
                return disstid, {"ok": True, "reason": "playlist_scan", "uin": target_uin, "playlist_id": disstid, "dirid": dirid, "playlist_name": name, "detail_meta": detail_meta}

        return 0, {"ok": False, "reason": "profile_homepage_mymusic_missing", "uin": target_uin, "map_meta": map_meta, "detail_meta": detail_meta, "playlist_total": len(playlists)}

    def get_playlist_tracks(self, disstid: int | str | None = None, limit: int = 1000, *, dirid: int | str | None = None, uin: str | int | None = None, ctx: int | None = None) -> list[dict[str, Any]]:
        tid = self._safe_int(disstid, 0) or None
        did = self._safe_int(dirid, 0) or None
        if tid is None and did is None:
            return []

        def _collect_tracks(payload: dict[str, Any]) -> list[dict[str, Any]]:
            candidates: list[Any] = []
            for container in (payload, payload.get("data") if isinstance(payload, dict) else None):
                if not isinstance(container, dict):
                    continue
                for key in ("songlist", "song_list", "songList", "songs"):
                    candidates.append(container.get(key))
                single_song = container.get("song")
                if isinstance(single_song, list):
                    candidates.append(single_song)
                elif isinstance(single_song, dict):
                    candidates.append([single_song])
                cdlist = container.get("cdlist")
                if isinstance(cdlist, list):
                    for cd_item in cdlist:
                        if not isinstance(cd_item, dict):
                            continue
                        for key in ("songlist", "song_list", "songList", "songs"):
                            candidates.append(cd_item.get(key))
                        single_song = cd_item.get("song")
                        if isinstance(single_song, list):
                            candidates.append(single_song)
                        elif isinstance(single_song, dict):
                            candidates.append([single_song])
            tracks: list[dict[str, Any]] = []
            seen: set[str] = set()
            for bucket in candidates:
                if not isinstance(bucket, list):
                    continue
                for item in bucket:
                    normalized = self._normalize_song(item if isinstance(item, dict) else {})
                    if not normalized:
                        continue
                    mid = str(normalized.get("mid") or "").strip()
                    if not mid or mid in seen:
                        continue
                    seen.add(mid)
                    tracks.append({
                        "mid": normalized["mid"],
                        "media_mid": normalized["media_mid"],
                        "title": normalized["title"],
                        "artist": normalized["artist"],
                        "duration_ms": normalized["duration_ms"],
                        "raw": normalized.get("raw") or item,
                    })
                    if len(tracks) >= int(limit or 1000):
                        return tracks
            return tracks

        uin_text = self._normalize_uin(str(uin or self._uin_from_cookies() or "0"))
        if tid is None and did in self._LIKED_DIRID_CANDIDATES:
            tid, _meta = self._fetch_myfav_map(uin_text)
            if tid is None or tid <= 0:
                tid, _meta = self._resolve_myfav_dissid(uin=uin_text)
        if tid is None and did is not None and uin_text != "0":
            for item in self.get_user_playlists(uin=uin_text, size=256):
                if self._safe_int(item.get("dirid"), 0) == did:
                    mapped_tid = self._safe_int(item.get("disstid"), 0)
                    if mapped_tid > 0:
                        tid = mapped_tid
                        break

        exact_variants: list[tuple[dict[str, Any], dict[str, str]]] = []
        if tid is not None:
            exact_base = {"type": 1, "json": 1, "utf8": 1, "onlysong": 0, "disstid": tid, "format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0}
            exact_variants.append((dict(exact_base, loginUin=0), self._playlist_headers(tid)))
            if uin_text != "0":
                exact_variants.append((dict(exact_base, loginUin=uin_text, hostUin=uin_text, uin=uin_text), self._playlist_headers(tid)))
        if did is not None:
            dirid_base = {"type": 1, "json": 1, "utf8": 1, "onlysong": 0, "dirid": did, "format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0}
            exact_variants.append((dict(dirid_base, loginUin=0), self._playlist_headers(tid)))
            if uin_text != "0":
                exact_variants.append((dict(dirid_base, loginUin=uin_text, hostUin=uin_text, uin=uin_text), self._playlist_headers(tid)))

        seen_signature: set[tuple[tuple[str, str], ...]] = set()
        for params, headers in exact_variants:
            signature = tuple(sorted((str(key), str(value)) for key, value in params.items() if key in {"disstid", "dirid", "loginUin", "hostUin", "uin"}))
            if signature in seen_signature:
                continue
            seen_signature.add(signature)
            try:
                payload, _status = self._request_json_text_payload(self._PLAYLIST_DETAIL_URL, params=params, headers=headers)
            except Exception:
                continue
            tracks = _collect_tracks(payload if isinstance(payload, dict) else {})
            if tracks:
                return tracks

        base_params = {"type": 1, "json": 1, "utf8": 1, "onlysong": 0, "new_format": 1, "song_begin": 0, "song_num": max(1, min(3000, int(limit or 1000))), "format": "json", "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 0}
        if uin_text != "0":
            base_params.update({"hostUin": uin_text, "loginUin": uin_text, "uin": uin_text})

        ctx_variants: list[int | None] = []
        for item in (ctx, 1, 0, None):
            if item not in ctx_variants:
                ctx_variants.append(item)
        request_variants: list[dict[str, Any]] = []
        for ctx_value in ctx_variants:
            for use_tid, use_did in ((tid, did), (tid, None), (None, did)):
                if use_tid is None and use_did is None:
                    continue
                params = dict(base_params)
                if use_tid is not None:
                    params["disstid"] = use_tid
                if use_did is not None:
                    params["dirid"] = use_did
                if ctx_value in {0, 1}:
                    params["ctx"] = ctx_value
                request_variants.append(params)

        for params in request_variants:
            signature = tuple(sorted((str(key), str(value)) for key, value in params.items() if key in {"disstid", "dirid", "ctx", "hostUin", "loginUin", "uin"}))
            if signature in seen_signature:
                continue
            seen_signature.add(signature)
            try:
                payload, _status = self._request_json_text_payload(
                    self._PLAYLIST_DETAIL_URL,
                    params=params,
                    headers=self._playlist_headers(self._safe_int(params.get("disstid"), 0) or tid),
                )
            except Exception:
                continue
            tracks = _collect_tracks(payload if isinstance(payload, dict) else {})
            if tracks:
                return tracks

        if tid is not None:
            legacy_variants = []
            for onlysong in (0, 1):
                for host_uin, login_uin in ((uin_text if uin_text != "0" else 0, uin_text if uin_text != "0" else 0), (0, 0)):
                    legacy_variants.append({
                        "type": 1,
                        "json": 1,
                        "utf8": 1,
                        "onlysong": onlysong,
                        "nosign": 1,
                        "disstid": tid,
                        "format": "json",
                        "inCharset": "GB2312",
                        "outCharset": "utf-8",
                        "notice": 0,
                        "platform": "yqq",
                        "needNewCode": 0,
                        "loginUin": login_uin,
                        "hostUin": host_uin,
                    })
            for legacy_params in legacy_variants:
                try:
                    payload, _status = self._request_json_text_payload(
                        self._PLAYLIST_DETAIL_LEGACY_URL,
                        params=legacy_params,
                        headers=self._playlist_headers(tid),
                    )
                    tracks = _collect_tracks(payload if isinstance(payload, dict) else {})
                    if tracks:
                        return tracks
                except Exception:
                    continue
        return []

    def _playlist_score(self, playlist: dict[str, Any], myfav_dissid: int = 0) -> tuple[int, int]:
        name = str(playlist.get("name") or "").strip().lower()
        dirid = self._safe_int(playlist.get("dirid"), 0)
        disstid = self._safe_int(playlist.get("disstid"), 0)
        song_count = self._safe_int(playlist.get("song_count"), 0)
        score = 0
        if dirid in self._LIKED_DIRID_CANDIDATES:
            score += 100
        if myfav_dissid > 0 and disstid == myfav_dissid:
            score += 90
        if name in {"我喜欢的音乐", "喜欢的音乐"}:
            score += 80
        elif any(hint in name for hint in self._LIKED_NAME_HINTS):
            score += 40
        score += min(song_count, 30)
        return score, song_count

    def get_liked_tracks(self, limit: int = 32) -> list[dict[str, Any]]:
        max_items = max(1, int(limit or 32))
        uin = self._uin_from_cookies()
        if uin == "0":
            self._last_liked_meta = {"ok": False, "reason": "no_uin"}
            return []

        direct_tried: list[dict[str, Any]] = []
        for dirid in self._LIKED_DIRID_CANDIDATES:
            for ctx_value in (1, 0, None):
                tracks = self.get_playlist_tracks(dirid=dirid, uin=uin, ctx=ctx_value, limit=max_items * 4)
                direct_tried.append({"dirid": dirid, "ctx": ctx_value, "count": len(tracks)})
                if tracks:
                    self._last_liked_meta = {"ok": True, "reason": f"direct_dirid_{dirid}", "dirid": dirid, "ctx": ctx_value, "count": len(tracks), "tried": direct_tried}
                    return tracks[:max_items]

        myfav_dissid, myfav_meta = self._resolve_myfav_dissid(uin=uin)
        if myfav_dissid > 0:
            for ctx_value in (1, 0, None):
                tracks = self.get_playlist_tracks(disstid=myfav_dissid, uin=uin, ctx=ctx_value, limit=max_items * 4)
                if tracks:
                    self._last_liked_meta = {"ok": True, "reason": "profile_myfav_dissid", "playlist_id": myfav_dissid, "ctx": ctx_value, "count": len(tracks), "myfav_meta": myfav_meta, "tried": direct_tried}
                    return tracks[:max_items]

        playlists = self.get_user_playlists(uin=uin, size=256)
        if not playlists:
            self._last_liked_meta = {"ok": False, "reason": "playlist_empty", "uin": uin, "myfav_meta": myfav_meta, "tried": direct_tried}
            return []

        tried: list[dict[str, Any]] = []
        sorted_playlists = sorted(playlists, key=lambda row: self._playlist_score(row, myfav_dissid=myfav_dissid), reverse=True)
        for item in sorted_playlists[:20]:
            disstid = self._safe_int(item.get("disstid"), 0)
            dirid = self._safe_int(item.get("dirid"), 0)
            name = str(item.get("name") or "").strip()
            tracks = []
            for ctx_value in (1, 0, None):
                tracks = self.get_playlist_tracks(disstid=disstid or None, dirid=dirid or None, uin=uin, ctx=ctx_value, limit=max_items * 4)
                tried.append({"disstid": disstid, "dirid": dirid, "ctx": ctx_value, "name": name, "count": len(tracks)})
                if tracks:
                    reason = "candidate_playlist"
                    lowered_name = name.lower()
                    if lowered_name in {"我喜欢的音乐", "喜欢的音乐"}:
                        reason = "playlist_name_exact"
                    elif any(hint in lowered_name for hint in self._LIKED_NAME_HINTS):
                        reason = "playlist_name_hint"
                    elif disstid == myfav_dissid:
                        reason = "playlist_id_match"
                    self._last_liked_meta = {"ok": True, "reason": reason, "playlist_id": disstid, "dirid": dirid, "playlist_name": name, "ctx": ctx_value, "count": len(tracks), "myfav_meta": myfav_meta, "tried": direct_tried + tried}
                    return tracks[:max_items]

        reason = "liked_playlist_not_found"
        if not self._has_music_auth_cookie():
            names = {self._clean_text(item.get("name")).lower() for item in playlists if isinstance(item, dict)}
            if names.issubset({"默认列表", "qzone背景音乐"}) or names == {"默认列表", "qzone背景音乐"}:
                reason = "missing_music_auth_cookie"
        self._last_liked_meta = {
            "ok": False,
            "reason": reason,
            "uin": uin,
            "myfav_meta": myfav_meta,
            "playlist_total": len(playlists),
            "tried": direct_tried + tried,
            "has_music_auth_cookie": self._has_music_auth_cookie(),
            "auth_cookie_keys": [key for key in ("qqmusic_key", "music_key", "qm_keyst") if self._cookie_text(key)],
        }
        return []



QQMusic = QQmisic
_instance: QQmisic | None = None


def get_qqmusic_client() -> QQmisic:
    global _instance
    if _instance is None:
        _instance = QQmisic()
    return _instance

