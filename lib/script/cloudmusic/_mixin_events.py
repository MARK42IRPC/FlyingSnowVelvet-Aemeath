"""网易云音乐管理器 - 播放队列事件路由 Mixin"""

import random
import threading
import re
import time
from pathlib import Path

from lib.core.event.center import EventType, Event
from lib.core.logger import get_logger
from config.config import TIMEOUTS, CLOUD_MUSIC
from config.music import get_music_history
from ._provider_clients import get_kugou_provider_client, get_qqmusic_provider_client
from ._constants import _AUDIO_EXT_CANDIDATES, _PROJECT_ROOT, make_local_track_ref

logger = get_logger(__name__)
_DURATION_TEXT_RE = re.compile(r"^\s*(\d{1,3}):(\d{2})\s*$")
_LIKED_ENQUEUE_LIMIT = 32


class _EventsMixin:
    """非登录事件处理：队列操作、播放控制、喜欢列表。"""

    @staticmethod
    def _format_duration_text(duration_ms: int | None) -> str:
        if duration_ms is None:
            return "00:00"
        try:
            if isinstance(duration_ms, str):
                m = _DURATION_TEXT_RE.match(duration_ms)
                if m:
                    total_sec = int(m.group(1)) * 60 + int(m.group(2))
                else:
                    total_sec = max(0, int(float(duration_ms)) // 1000)
            elif isinstance(duration_ms, dict):
                raw = (
                    duration_ms.get('duration_ms')
                    or duration_ms.get('duration')
                    or duration_ms.get('dt')
                    or duration_ms.get('ms')
                )
                total_sec = max(0, int(raw) // 1000) if raw is not None else 0
            else:
                total_sec = max(0, int(duration_ms) // 1000)
        except (TypeError, ValueError):
            return "00:00"
        mins, secs = divmod(total_sec, 60)
        return f"{mins:02d}:{secs:02d}"

    @staticmethod
    def _first_artist_name(artists) -> str:
        if not artists:
            return "未知作者"
        first = artists[0]
        if isinstance(first, dict):
            name = str(first.get("name") or "").strip()
            return name or "未知作者"
        name = str(first).strip()
        return name or "未知作者"

    def _build_song_display(self, title: str, artist: str, duration_ms: int | None = None) -> str:
        duration = self._format_duration_text(duration_ms)
        clean_title = str(title or "").strip() or "未知歌曲"
        clean_artist = str(artist or "").strip() or "未知作者"
        return f"{duration} {clean_title} - {clean_artist}"

    @staticmethod
    def _normalize_track_ref(song_ref):
        """兼容抽象层 track_ref 与历史 song_id。"""
        raw = song_ref
        if isinstance(raw, int):
            return raw & 0xFFFFFFFF if raw < 0 else raw
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            if text.startswith("local::"):
                return text
            if ":" in text:
                provider, sid_text = text.split(":", 1)
                provider = provider.strip().lower()
                sid_text = sid_text.strip()
                if provider == "netease":
                    try:
                        sid = int(sid_text)
                    except (TypeError, ValueError):
                        return None
                    return sid & 0xFFFFFFFF if sid < 0 else sid
                if provider == "qq":
                    return f"qq:{sid_text}" if sid_text else None
                if provider == "kugou":
                    if not sid_text:
                        return None
                    parts = sid_text.split(":")
                    parts[0] = str(parts[0] or "").upper()
                    normalized = ":".join(parts)
                    return f"kugou:{normalized}" if normalized else None
                return None
            try:
                sid = int(text)
            except (TypeError, ValueError):
                return None
            return sid & 0xFFFFFFFF if sid < 0 else sid
        return None

    @staticmethod
    def _resolve_local_music_dir() -> Path | None:
        raw = str(CLOUD_MUSIC.get("local_music_dir", "") or "").strip()
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @staticmethod
    def _safe_tag_text(raw) -> str:
        value = raw
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if hasattr(value, "text"):
            value = getattr(value, "text")
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
        text = str(value or "").strip()
        return text

    @classmethod
    def _pick_tag_text(cls, tags, keys: tuple[str, ...]) -> str:
        if tags is None:
            return ""
        getter = getattr(tags, "get", None)
        if not callable(getter):
            return ""
        for key in keys:
            try:
                raw = getter(key)
            except Exception:
                raw = None
            text = cls._safe_tag_text(raw)
            if text:
                return text
        return ""

    @classmethod
    def _read_local_audio_meta(cls, file_path: Path) -> tuple[str, str, int | None]:
        title = file_path.stem
        artist = "未知作者"
        duration_ms: int | None = None

        try:
            from mutagen import File as MutagenFile

            audio = MutagenFile(str(file_path))
            if audio is not None:
                info = getattr(audio, "info", None)
                length = float(getattr(info, "length", 0.0) or 0.0)
                if length > 0:
                    duration_ms = int(length * 1000)

                tags = getattr(audio, "tags", None)
                tag_title = cls._pick_tag_text(tags, ("title", "TIT2", "\xa9nam"))
                tag_artist = cls._pick_tag_text(tags, ("artist", "TPE1", "albumartist", "aART", "\xa9ART"))
                if tag_title:
                    title = tag_title
                if tag_artist:
                    artist = tag_artist
        except Exception:
            pass

        if duration_ms is None and file_path.suffix.lower() == ".wav":
            try:
                import wave
                with wave.open(str(file_path), "rb") as wav_file:
                    frame_rate = int(wav_file.getframerate() or 0)
                    frame_count = int(wav_file.getnframes() or 0)
                if frame_rate > 0 and frame_count > 0:
                    duration_ms = int(frame_count * 1000 / frame_rate)
            except Exception:
                pass

        safe_title = str(title or file_path.stem).strip() or file_path.stem
        safe_artist = str(artist or "未知作者").strip() or "未知作者"
        return safe_title, safe_artist, duration_ms

    def _scan_local_tracks(self, local_dir: Path) -> list[tuple[str, str]]:
        ext_set = {str(ext).lower() for ext in _AUDIO_EXT_CANDIDATES}
        files: list[Path] = []
        try:
            for path in local_dir.rglob("*"):
                try:
                    if path.is_file() and path.suffix.lower() in ext_set:
                        files.append(path)
                except OSError:
                    continue
        except Exception as e:
            logger.error("[CloudMusic] 扫描本地音乐目录失败: %s", e)
            return []
        files.sort(key=lambda p: str(p).lower())

        tracks: list[tuple[str, str]] = []
        for file_path in files:
            title, artist, duration_ms = self._read_local_audio_meta(file_path)
            display = self._build_song_display(title, artist, duration_ms)
            tracks.append((make_local_track_ref(file_path), display))
        return tracks

    # ------------------------------------------------------------------
    # 队列操作
    # ------------------------------------------------------------------

    def _on_play_top(self, event: Event):
        """处理 MUSIC_PLAY_TOP 事件：左键立即播放，替换当前曲目但不影响队列。"""
        song_ref = event.data.get('track_ref', event.data.get('song_id'))
        song_id = self._normalize_track_ref(song_ref)
        display = event.data.get('display', '')
        if song_id is None:
            logger.warning("[CloudMusic] 无效 PLAY_TOP song_ref=%s", song_ref)
            self._show_error("歌曲标识无效，无法播放")
            return

        logger.debug("[CloudMusic] 收到 PLAY_TOP: song_id=%s, display=%s", song_id, display)

        self._stop_internal()

        if self._queue and 0 <= self._current_index < len(self._queue):
            self._queue[self._current_index] = (song_id, display)
        else:
            self._queue.insert(0, (song_id, display))
            self._current_index = 0

        self._play_current()

    def _on_enqueue(self, event: Event):
        """处理 MUSIC_ENQUEUE 事件：右键加入队列末尾。"""
        song_ref = event.data.get('track_ref', event.data.get('song_id'))
        song_id = self._normalize_track_ref(song_ref)
        display = event.data.get('display', '')
        if song_id is None:
            logger.warning("[CloudMusic] 无效 ENQUEUE song_ref=%s", song_ref)
            self._show_error("歌曲标识无效，无法加入队列")
            return

        logger.debug("[CloudMusic] 收到 ENQUEUE: song_id=%s, display=%s", song_id, display)

        self._queue.append((song_id, display))
        self._show_info(f"已加入播放队列")

        if not self._is_playing and self._current_index == -1:
            self._current_index = 0
            self._play_current()

    def _on_enqueue_history(self, event: Event):
        """处理 MUSIC_ENQUEUE_HISTORY 事件：将历史歌曲批量追加到队列末尾。"""
        history_items = get_music_history(self._current_provider()).get_all()
        if not history_items:
            self._show_info("历史记录为空")
            return

        appended = 0
        for item in history_items:
            song_id = self._normalize_track_ref(item.get("id"))
            if song_id is None:
                continue

            title = str(item.get("title") or "").strip() or str(song_id)
            artist = str(item.get("artist") or "").strip() or "未知作者"
            display = self._build_song_display(title, artist, item.get("duration_ms"))

            self._queue.append((song_id, display))
            appended += 1

        if appended <= 0:
            self._show_info("历史记录格式无效")
            return

        logger.info("[CloudMusic] 历史入队完成: appended=%d", appended)
        self._show_info(f"已追加 {appended} 首历史歌曲")

        with self._state_lock:
            should_autoplay = (
                not self._is_playing
                and not self._is_paused
                and bool(self._queue)
            )
            if should_autoplay and not (0 <= self._current_index < len(self._queue)):
                self._current_index = 0

        if should_autoplay:
            self._play_current()
            return

        self._publish_status()

    def _on_play_queue_index(self, event: Event):
        """处理 MUSIC_PLAY_QUEUE_INDEX：将指定队列项置顶并立即播放。"""
        index_raw = event.data.get('index', -1)
        try:
            index = int(index_raw)
        except (TypeError, ValueError):
            return

        with self._state_lock:
            queue_len = len(self._queue)
        if not (0 <= index < queue_len):
            return

        # 先停止当前播放，避免旧监控线程与下载线程残留。
        self._stop_internal()

        with self._state_lock:
            queue_len = len(self._queue)
            if not (0 <= index < queue_len):
                return
            if index != 0:
                song = self._queue.pop(index)
                self._queue.insert(0, song)
            self._current_index = 0
            display = self._queue[0][1] if self._queue else ''

        if display:
            self._show_info(f"立即播放: {display}")
        self._play_current()

    def _on_enqueue_local(self, event: Event):
        """处理 MUSIC_ENQUEUE_LOCAL 事件：清空并载入本地音乐文件夹。"""
        self._stop_internal()
        with self._state_lock:
            self._queue.clear()
            self._current_index = -1
            self._is_playing = False
            self._is_paused = False
        self._publish_status()
        self._show_info("正在加载本地音乐...")

        threading.Thread(
            target=self._enqueue_local_worker,
            daemon=True,
            name="cm-local-load",
        ).start()

    def _enqueue_local_worker(self):
        local_dir = self._resolve_local_music_dir()
        if local_dir is None:
            self._show_info("没有找到本地音乐,请检查控制面板的音乐路径")
            return
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("[CloudMusic] 创建本地音乐目录失败: %s", e)
            self._show_error("本地音乐目录不可用，请检查配置")
            return

        tracks = self._scan_local_tracks(local_dir)
        if not tracks:
            self._show_info("没有找到本地音乐,请检查控制面板的音乐路径")
            return

        with self._state_lock:
            self._queue = tracks
            self._current_index = 0
            self._is_playing = False
            self._is_paused = False
        self._publish_status()
        self._show_info(f"已载入本地音乐 {len(tracks)} 首")
        self._play_current()

    # ------------------------------------------------------------------
    # 喜欢列表
    # ------------------------------------------------------------------

    @staticmethod
    def _qq_liked_fail_message(meta: dict | None) -> str:
        info = meta if isinstance(meta, dict) else {}
        reason = str(info.get("reason") or "").strip()
        if reason == "no_uin":
            return "QQ 平台未登录或登录态失效，请重新登录后重试"
        if reason == "missing_music_auth_cookie":
            return "当前 QQ 登录态缺少 QQ 音乐专用凭据，请重新点击 QQ 登录，并在内置浏览器中完成扫码后重试"
        if reason in {
            "playlist_request_failed",
            "playlist_empty",
            "playlist_no_candidate",
            "playlist_tracks_empty",
            "liked_playlist_not_found",
            "liked_playlist_tracks_empty",
        }:
            return "QQ 喜欢列表为空或不可访问，请检查账号歌单权限后重试"
        return "QQ 喜欢列表为空或登录态已过期，请重新登录后重试"

    @staticmethod
    def _kugou_liked_fail_message(meta: dict | None) -> str:
        info = meta if isinstance(meta, dict) else {}
        reason = str(info.get("reason") or "").strip()
        err_code = str(info.get("err_code") or "").strip()
        if reason in {"not_logged_in", "no_login_token"}:
            return "酷狗平台未登录或登录态失效，请重新登录后重试"
        if err_code == "30020":
            return "酷狗喜欢列表触发安全验证，请先完成验证后重试"
        if reason in {"request_failed", "invalid_data", "items_not_list"}:
            return "酷狗喜欢列表请求失败，请稍后重试"
        return "酷狗喜欢列表为空或登录态已过期，请重新登录后重试"

    def _on_enqueue_liked(self, event: Event):
        """处理 MUSIC_ENQUEUE_LIKED 事件：清空并随机加载喜欢列表（最多 32 首）。"""
        current_provider = str(self._current_provider()).strip().lower()
        provider_label = {
            "netease": "网易云",
            "qq": "QQ 音乐",
            "kugou": "酷狗音乐",
            "local": "本地模式",
        }.get(current_provider, "当前平台")

        try:
            logged_in = bool(self.provider_logged_in(current_provider))
        except Exception:
            with self._state_lock:
                logged_in = self._is_logged_in
        if not logged_in and current_provider == "qq":
            try:
                logged_in = bool(get_qqmusic_provider_client().is_logged_in())
                if logged_in:
                    self._set_login_state(True, {}, provider="qq")
            except Exception:
                logged_in = False
        if not logged_in and current_provider == "kugou":
            try:
                logged_in = bool(get_kugou_provider_client().is_logged_in())
                if logged_in:
                    self._set_login_state(True, {}, provider="kugou")
            except Exception:
                logged_in = False
        if not logged_in:
            self._show_info('请先登录音乐平台账号')
            return

        if current_provider not in {"netease", "qq", "kugou"}:
            self._show_info(f"{provider_label}暂不支持一键喜欢")
            return

        self._show_info(f'正在加载{provider_label}喜欢列表...')

        threading.Thread(
            target=self._enqueue_liked_worker,
            args=(current_provider,),
            daemon=True,
            name=f'cm-liked-load-{current_provider}',
        ).start()
    def _enqueue_liked_worker(self, provider: str):
        """后台线程：拉取各平台喜欢列表，随机入队并开始播放。"""
        try:
            if not self._login_ready.wait(timeout=TIMEOUTS['login_wait']):
                self._show_error('登录状态未就绪，请稍后重试')
                return

            provider_name = str(provider or "").strip().lower()
            if provider_name == "netease":
                tracks = self._fetch_liked_tracks(limit=_LIKED_ENQUEUE_LIMIT)
            elif provider_name == "qq":
                tracks = []
                for idx in range(2):
                    tracks = self._fetch_qq_liked_tracks(limit=_LIKED_ENQUEUE_LIMIT)
                    if tracks:
                        break
                    if idx == 0:
                        time.sleep(0.25)
            elif provider_name == "kugou":
                tracks = []
                for idx in range(3):
                    tracks = self._fetch_kugou_liked_tracks(limit=_LIKED_ENQUEUE_LIMIT)
                    if tracks:
                        break
                    if idx < 2:
                        time.sleep(0.3)
            else:
                tracks = []
            if not tracks:
                if provider_name == "qq":
                    meta = get_qqmusic_provider_client().get_last_liked_meta()
                    logger.info("[CloudMusic] QQ 喜欢列表为空 meta=%s", meta)
                    self._show_info(self._qq_liked_fail_message(meta))
                elif provider_name == "kugou":
                    meta = get_kugou_provider_client().get_last_liked_meta()
                    logger.info("[CloudMusic] 酷狗喜欢列表为空 meta=%s", meta)
                    self._show_info(self._kugou_liked_fail_message(meta))
                else:
                    self._show_info('喜欢列表为空')
                return

            random.shuffle(tracks)
            tracks = tracks[:_LIKED_ENQUEUE_LIMIT]

            self._stop_internal()
            with self._state_lock:
                self._queue         = tracks
                self._current_index = 0
                self._is_playing    = False
                self._is_paused     = False
            self._publish_status()
            self._show_info(f'已随机载入喜欢列表 {len(tracks)} 首')
            self._play_current()
        except ImportError:
            self._show_error('缺少依赖，无法加载喜欢列表')
        except Exception as e:
            logger.error('[CloudMusic] 加载喜欢列表失败: %s', e)
            self._show_error('加载喜欢列表失败，请稍后重试')

    def _fetch_liked_tracks(self, limit: int = _LIKED_ENQUEUE_LIMIT) -> list[tuple[int, str]]:
        """获取账号"我喜欢的音乐"歌单并转换为播放队列项。"""
        from pyncm.apis.login import GetCurrentLoginStatus
        from pyncm.apis import user as user_api, playlist as playlist_api

        status = self._call_with_cookie_recover(GetCurrentLoginStatus)
        if not self._is_account_logged_in(status):
            raise RuntimeError('当前未登录网易云账号')

        account = status.get('account') or {}
        profile = status.get('profile') or {}
        user_id = account.get('id') or profile.get('userId')
        if not user_id:
            raise RuntimeError('无法获取当前账号 ID')

        playlists_resp = user_api.GetUserPlaylists(int(user_id), limit=1000)
        playlists      = playlists_resp.get('playlist') or []
        if not playlists:
            raise RuntimeError('未获取到用户歌单列表')

        liked_playlist = None
        for p in playlists:
            try:
                if int(p.get('specialType') or 0) == 5:
                    liked_playlist = p
                    break
            except Exception:
                continue

        if liked_playlist is None:
            for p in playlists:
                name = str(p.get('name') or '').strip()
                if name in ('我喜欢的音乐', '喜欢的音乐'):
                    liked_playlist = p
                    break

        if liked_playlist is None:
            uid = int(user_id)
            for p in playlists:
                creator = p.get('creator') or {}
                cid = creator.get('userId') if isinstance(creator, dict) else None
                try:
                    if cid is not None and int(cid) == uid and not bool(p.get('subscribed', False)):
                        liked_playlist = p
                        break
                except Exception:
                    continue

        if liked_playlist is None:
            liked_playlist = playlists[0]

        playlist_id = liked_playlist.get('id')
        if not playlist_id:
            raise RuntimeError('喜欢歌单缺少有效 ID')

        detail = playlist_api.GetPlaylistAllTracks(int(playlist_id), offset=0, limit=1000)
        songs  = detail.get('songs') or []

        items: list[tuple[int, str]] = []
        seen:  set[int] = set()
        for song in songs:
            sid = song.get('id')
            if sid is None:
                continue
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                continue
            sid = sid & 0xFFFFFFFF if sid < 0 else sid
            if sid in seen:
                continue
            seen.add(sid)

            title = str(song.get('name') or sid).strip() or str(sid)
            artists = song.get('ar') or song.get('artists') or []
            first_artist = self._first_artist_name(artists)
            dt_ms = song.get('dt') or song.get('duration')
            display = self._build_song_display(title, first_artist, dt_ms)
            items.append((sid, display))

        return items

    def _fetch_qq_liked_tracks(self, limit: int = _LIKED_ENQUEUE_LIMIT) -> list[tuple[str, str]]:
        client = get_qqmusic_provider_client()
        songs = client.get_liked_tracks(limit=max(1, int(limit or _LIKED_ENQUEUE_LIMIT)))
        meta = client.get_last_liked_meta() if hasattr(client, "get_last_liked_meta") else {}
        if not songs:
            logger.info("[CloudMusic] QQ liked tracks empty meta=%s", meta)
            return []
        logger.info("[CloudMusic] QQ liked tracks source meta=%s", meta)
        items: list[tuple[str, str]] = []
        seen: set[str] = set()
        for song in songs:
            if not isinstance(song, dict):
                continue
            mid = str(song.get("mid") or song.get("songmid") or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            title = str(song.get("title") or song.get("name") or "未知歌曲").strip() or "未知歌曲"
            artist = str(song.get("artist") or "").strip()
            if not artist:
                artist = self._first_artist_name(song.get("singer") or song.get("singers") or [])
            duration_ms = song.get("duration_ms") or song.get("duration") or song.get("interval")
            try:
                duration_ms = int(duration_ms) if duration_ms is not None else None
                if duration_ms is not None and duration_ms > 0 and duration_ms < 1000:
                    duration_ms *= 1000
            except (TypeError, ValueError):
                duration_ms = None
            media_mid = str(song.get("media_mid") or "").strip()
            if not media_mid:
                raw_song = song.get("raw")
                if isinstance(raw_song, dict):
                    file_info = raw_song.get("file") if isinstance(raw_song.get("file"), dict) else {}
                    media_mid = str(file_info.get("media_mid") or "").strip()
            display = self._build_song_display(title, artist, duration_ms)
            track_ref = f"qq:{mid}:{media_mid}" if media_mid and media_mid != mid else f"qq:{mid}"
            items.append((track_ref, display))
            if len(items) >= int(limit):
                break
        return items

    def _fetch_kugou_liked_tracks(self, limit: int = _LIKED_ENQUEUE_LIMIT) -> list[tuple[str, str]]:
        client = get_kugou_provider_client()
        songs = client.get_liked_tracks(limit=max(1, int(limit or _LIKED_ENQUEUE_LIMIT)))
        if not songs:
            logger.info("[CloudMusic] Kugou liked tracks empty meta=%s", client.get_last_liked_meta())
            return []
        items: list[tuple[str, str]] = []
        seen: set[str] = set()
        for song in songs:
            if not isinstance(song, dict):
                continue
            song_hash = str(song.get("hash") or "").strip().upper()
            if not song_hash or song_hash in seen:
                continue
            seen.add(song_hash)
            title = str(song.get("title") or "未知歌曲").strip() or "未知歌曲"
            artist = str(song.get("artist") or "未知作者").strip() or "未知作者"
            duration_ms = song.get("duration_ms")
            try:
                duration_ms = int(duration_ms) if duration_ms is not None else None
            except (TypeError, ValueError):
                duration_ms = None
            display = self._build_song_display(title, artist, duration_ms)
            album_id = song.get("album_id")
            audio_id = song.get("audio_id") or song.get("album_audio_id")
            encode_mix = str(song.get("encode_album_audio_id") or "").strip()
            try:
                album_id_int = int(album_id) if album_id is not None else 0
            except (TypeError, ValueError):
                album_id_int = 0
            try:
                audio_id_int = int(audio_id) if audio_id is not None else 0
            except (TypeError, ValueError):
                audio_id_int = 0
            if encode_mix:
                track_ref = (
                    f"kugou:{song_hash}:{max(0, album_id_int)}:{max(0, audio_id_int)}:{encode_mix}"
                )
            elif album_id_int > 0 or audio_id_int > 0:
                track_ref = f"kugou:{song_hash}:{max(0, album_id_int)}:{max(0, audio_id_int)}"
            else:
                track_ref = f"kugou:{song_hash}"
            items.append((track_ref, display))
            if len(items) >= int(limit):
                break
        return items

    # ------------------------------------------------------------------
    # 播放控制
    # ------------------------------------------------------------------

    def _on_play_mode_toggle(self, event: Event):
        """处理 MUSIC_PLAY_MODE_TOGGLE 事件：循环切换播放模式。"""
        self.cycle_play_mode()

    def _on_play_pause(self, event: Event):
        """处理 MUSIC_PLAY_PAUSE 事件：暂停/继续播放。

        兼容两种语义：
        - 显式语义：event.data['playing'] 存在时，按该目标状态执行。
        - 切换语义：未提供 playing 时，自动在“暂停/播放”之间切换。
        """
        data = event.data or {}

        with self._state_lock:
            is_playing = self._is_playing
            is_paused = self._is_paused
            has_queue = bool(self._queue)

        if 'playing' in data:
            target_playing = bool(data.get('playing'))
        else:
            # 切换模式：暂停中 -> 继续；播放中 -> 暂停；空闲 -> 尝试播放
            target_playing = is_paused or not is_playing

        if target_playing:
            if is_paused:
                self._resume()
            elif (not is_playing) and has_queue:
                with self._state_lock:
                    if self._current_index == -1 and self._queue:
                        self._current_index = 0
                self._play_current()
        else:
            self.pause()

    def _on_next_track(self, event: Event):
        """处理 MUSIC_NEXT_TRACK 事件：播放下一首。"""
        if len(self._queue) <= 1:
            self._show_info("没有下一首歌曲")
            return
        self._play_next()

    def _on_volume(self, event: Event):
        """处理 MUSIC_VOLUME 事件：调整音量。

        支持两种参数：
        - delta:  相对调整量（正数增加，负数减少）
        - volume: 绝对值设定（0.0-1.0），优先级高于 delta
        """
        if 'volume' in event.data:
            self.set_volume(event.data['volume'])
        else:
            delta = event.data.get('delta', 0.0)
            self.set_volume(self._volume + delta)

    def _on_seek(self, event: Event):
        """处理 MUSIC_SEEK 事件：跳转播放进度。

        支持两种参数：
        - progress: 进度百分比 (0.0 - 1.0)，由进度条发送
        - pos_sec:  具体秒数（兼容旧逻辑）
        """
        progress = event.data.get('progress')
        if progress is not None:
            duration_ms = self._get_current_duration()
            if duration_ms <= 0:
                return
            pos_sec = progress * duration_ms / 1000.0
        else:
            pos_sec     = event.data.get('pos_sec', 0)
            duration_ms = self._current_duration_ms
            if duration_ms > 0:
                progress = pos_sec * 1000 / duration_ms

        if pos_sec < 0:
            return
        try:
            import pygame
            pygame.mixer.music.set_pos(pos_sec)
            self._seek_offset_ms = int(pos_sec * 1000)
            if progress is not None:
                self._last_seek_progress = progress
            logger.debug(
                "[CloudMusic] SEEK: 目标progress=%.4f, pos_sec=%.2f, duration_ms=%d, seek_offset=%dms",
                progress if progress else 0,
                pos_sec,
                self._current_duration_ms,
                self._seek_offset_ms,
            )
        except Exception as e:
            logger.error("[CloudMusic] seek 失败: %s", e)

    # ------------------------------------------------------------------
    # 粒子生成响应
    # ------------------------------------------------------------------

    def _on_speaker_window_response(self, event: Event):
        """处理音响窗口范围响应事件，生成音符粒子。"""
        rects = event.data.get('rects', [])
        if not rects:
            return

        selected_rect = random.choice(rects)
        x1, y1, x2, y2 = selected_rect

        self._ec.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'music_note',
            'area_type':   'rect',
            'area_data':   (x1, y1, x2, y2),
        }))
