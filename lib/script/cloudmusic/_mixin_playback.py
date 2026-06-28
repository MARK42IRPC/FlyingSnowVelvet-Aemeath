"""网易云音乐管理器 - 播放、下载与进度控制 Mixin"""

import threading
import time
import random
import re
import wave
from pathlib import Path
from urllib.parse import urlparse

import requests

from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import EventType, Event
from lib.core.logger import get_logger
from config.audio import get_effective_music_volume
from config.config import TIMEOUTS
from config.music import get_music_history
from ._provider_clients import get_kugou_provider_client, get_qqmusic_provider_client

from ._constants import (
    _BITRATE_LADDER,
    _PARTICLE_INTERVAL,
    is_local_track_ref,
    local_track_path_from_ref,
)

logger = get_logger(__name__)
_DISPLAY_PREFIX_RE = re.compile(r"^(?:\d{2}:\d{2}|--:--)\s+(.*)$")


class _PlaybackMixin:
    """播放控制、下载、进度跟踪与 pygame 集成。"""

    def _read_duration_ms(self, target_path: Path) -> int:
        try:
            from mutagen import File as MutagenFile

            audio = MutagenFile(str(target_path))
            length = float(getattr(getattr(audio, "info", None), "length", 0.0) or 0.0)
            if length > 0:
                return int(length * 1000)
        except Exception:
            pass

        if target_path.suffix.lower() == ".wav":
            try:
                with wave.open(str(target_path), "rb") as wav_file:
                    frame_rate = int(wav_file.getframerate() or 0)
                    frame_count = int(wav_file.getnframes() or 0)
                if frame_rate > 0 and frame_count > 0:
                    return int(frame_count * 1000 / frame_rate)
            except Exception:
                pass
        return 0

    def _cache_duration_probe_result(self, song_id, duration_ms: int) -> None:
        if duration_ms <= 0:
            return
        self._duration_cache[song_id] = duration_ms
        if self._queue and 0 <= self._current_index < len(self._queue):
            current_song_id, _ = self._queue[self._current_index]
            if current_song_id == song_id:
                self._current_duration_ms = duration_ms

    def _schedule_duration_probe(self, song_id, target_path: Path) -> None:
        if song_id in self._duration_cache:
            return

        def _probe() -> None:
            duration_ms = self._read_duration_ms(target_path)
            if duration_ms > 0:
                self._cache_duration_probe_result(song_id, duration_ms)

        get_compute_hub().submit_latest(
            f"cloudmusic_duration_{song_id}",
            _probe,
            executor="io",
        )

    @staticmethod
    def _is_qq_track_ref(song_id) -> bool:
        return isinstance(song_id, str) and str(song_id).startswith("qq:")

    @staticmethod
    def _qq_mid_from_track_ref(song_id) -> str | None:
        if not isinstance(song_id, str):
            return None
        text = song_id.strip()
        if text.startswith("qq:"):
            text = text.split(":", 1)[1].strip()
        parts = text.split(":")
        mid = str(parts[0] or "").strip()
        return mid or None

    @staticmethod
    def _qq_media_mid_from_track_ref(song_id) -> str | None:
        if not isinstance(song_id, str):
            return None
        text = song_id.strip()
        if text.startswith("qq:"):
            text = text.split(":", 1)[1].strip()
        parts = text.split(":")
        if len(parts) < 2:
            return None
        media_mid = str(parts[1] or "").strip()
        return media_mid or None

    @staticmethod
    def _is_kugou_track_ref(song_id) -> bool:
        return isinstance(song_id, str) and str(song_id).startswith("kugou:")

    @staticmethod
    def _kugou_hash_from_track_ref(song_id) -> str | None:
        if not isinstance(song_id, str):
            return None
        text = song_id.strip()
        if not text.startswith("kugou:"):
            return None
        raw = text.split(":", 1)[1].strip()
        if not raw:
            return None
        # 兼容未来扩展形态 kugou:{hash}:{extra}
        song_hash = raw.split(":", 1)[0].strip()
        return song_hash.upper() if song_hash else None

    @staticmethod
    def _kugou_album_id_from_track_ref(song_id) -> int | None:
        if not isinstance(song_id, str):
            return None
        text = song_id.strip()
        if not text.startswith("kugou:"):
            return None
        raw = text.split(":", 1)[1].strip()
        if not raw:
            return None
        parts = raw.split(":")
        if len(parts) < 2:
            return None
        try:
            album_id = int(str(parts[1] or "").strip())
        except (TypeError, ValueError):
            return None
        return album_id if album_id > 0 else None

    @staticmethod
    def _kugou_audio_id_from_track_ref(song_id) -> int | None:
        if not isinstance(song_id, str):
            return None
        text = song_id.strip()
        if not text.startswith("kugou:"):
            return None
        raw = text.split(":", 1)[1].strip()
        if not raw:
            return None
        parts = raw.split(":")
        if len(parts) < 3:
            return None
        try:
            audio_id = int(str(parts[2] or "").strip())
        except (TypeError, ValueError):
            return None
        return audio_id if audio_id > 0 else None

    @staticmethod
    def _kugou_encode_mix_id_from_track_ref(song_id) -> str | None:
        if not isinstance(song_id, str):
            return None
        text = song_id.strip()
        if not text.startswith("kugou:"):
            return None
        raw = text.split(":", 1)[1].strip()
        if not raw:
            return None
        parts = raw.split(":")
        if len(parts) < 4:
            return None
        mix_id = str(parts[3] or "").strip()
        return mix_id or None

    @staticmethod
    def _normalize_url_candidates(raw_urls) -> list[str]:
        """将字符串/列表统一为去重后的 URL 列表。"""
        out: list[str] = []
        seen: set[str] = set()
        if isinstance(raw_urls, str):
            values = [raw_urls]
        elif isinstance(raw_urls, list):
            values = raw_urls
        else:
            values = []
        for item in values:
            url = str(item or "").strip()
            if not url:
                continue
            if url.startswith("//"):
                url = f"https:{url}"
            if not (url.startswith("http://") or url.startswith("https://")):
                continue
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
        return out

    @staticmethod
    def _is_name_resolution_error(error: Exception) -> bool:
        text = str(error or "").lower()
        if not text:
            return False
        return (
            "nameresolutionerror" in text
            or "failed to resolve" in text
            or "getaddrinfo failed" in text
            or "name or service not known" in text
        )

    @staticmethod
    def _http_status_from_error(error: Exception) -> int | None:
        """从请求异常中提取 HTTP 状态码。"""
        if isinstance(error, requests.HTTPError):
            resp = getattr(error, "response", None)
            if resp is not None:
                try:
                    status = int(resp.status_code)
                    return status if status > 0 else None
                except Exception:
                    pass
        text = str(error or "")
        match = re.search(r"\b([1-5]\d{2})\b", text)
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    @staticmethod
    def _download_base_headers() -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }

    def _download_session_for_song(self, song_id):
        """按平台选择会话，尽量复用平台 Cookie/指纹。"""
        try:
            if self._is_kugou_track_ref(song_id):
                return get_kugou_provider_client().get_session()
            if self._is_qq_track_ref(song_id):
                return get_qqmusic_provider_client().get_session()
        except Exception:
            pass
        return requests

    def _download_header_profiles(self, song_id, url: str) -> list[dict[str, str]]:
        """为下载请求构建多组请求头，403 时按顺序回退。"""
        host = str(urlparse(url).hostname or "").strip().lower()
        profiles: list[dict[str, str]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()

        def _add(referer: str = "", origin: str = "") -> None:
            headers = dict(self._download_base_headers())
            if referer:
                headers["Referer"] = referer
            if origin:
                headers["Origin"] = origin
            signature = tuple(sorted(headers.items()))
            if signature in seen:
                return
            seen.add(signature)
            profiles.append(headers)

        if self._is_kugou_track_ref(song_id):
            kuwo_ref = "https://www.kuwo.cn/"
            kugou_ref = "https://www.kugou.com/"
            if "kuwo.cn" in host:
                _add(kuwo_ref, "https://www.kuwo.cn")
                _add(kuwo_ref)
                _add(kugou_ref, "https://www.kugou.com")
                _add(kugou_ref)
            else:
                _add(kugou_ref, "https://www.kugou.com")
                _add(kugou_ref)
                _add(kuwo_ref, "https://www.kuwo.cn")
                _add(kuwo_ref)
            _add("https://m.kugou.com/", "https://m.kugou.com")
            _add("https://m.kugou.com/")
        elif self._is_qq_track_ref(song_id):
            _add("https://y.qq.com/", "https://y.qq.com")
        else:
            _add("https://music.163.com/", "https://music.163.com")
            _add("https://music.163.com/")

        # 最后兜底：仅带基础头，避免 Referer/Origin 触发部分 CDN 风控。
        _add()

        if not profiles:
            _add()
        return profiles

    def _append_unique_urls(self, target: list[str], raw_urls) -> bool:
        appended = False
        for url in self._normalize_url_candidates(raw_urls):
            if url in target:
                continue
            target.append(url)
            appended = True
        return appended

    @staticmethod
    def _build_play_display(display: str, title: str, artist: str, *, default_artist: str = "未知作者") -> str:
        play_display = str(display or "").strip()
        if play_display:
            return play_display
        safe_title = str(title or "").strip()
        safe_artist = str(artist or "").strip() or default_artist
        return f"--:-- {safe_title} - {safe_artist}"

    def _kugou_url_candidates(
        self,
        client,
        song_hash: str,
        *,
        album_id=None,
        audio_id=None,
        encode_mix=None,
        detail=None,
    ) -> list[str]:
        url_candidates: list[str] = []
        detail = detail or {}
        fresh_url = str(
            client.get_song_url(
                song_hash,
                album_id=album_id,
                album_audio_id=audio_id,
                encode_album_audio_id=encode_mix,
            ) or ""
        ).strip()
        self._append_unique_urls(url_candidates, fresh_url)
        self._append_unique_urls(url_candidates, detail.get("url_candidates"))
        self._append_unique_urls(url_candidates, str(detail.get("url") or "").strip())
        return url_candidates

    @staticmethod
    def _safe_pyncm_call(call, *args, **kwargs):
        """统一走登录 Mixin 的 Cookie 冲突恢复逻辑。"""
        try:
            return call(*args, **kwargs)
        except TypeError:
            # 兼容 call 签名异常场景，回退原始调用。
            func = args[0] if args else None
            if callable(func):
                return func(*args[1:], **kwargs)
            raise

    @staticmethod
    def _netease_audio_entry(result: dict) -> dict:
        if not isinstance(result, dict):
            return {}
        data = result.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                return first
        return {}

    @staticmethod
    def _netease_fail_reason(entry: dict, fallback: str = "") -> str:
        if not isinstance(entry, dict):
            return str(fallback or "").strip()
        parts: list[str] = []
        code = entry.get("code")
        if code is not None:
            parts.append(f"code={code}")
        fee = entry.get("fee")
        if fee is not None:
            parts.append(f"fee={fee}")
        level = entry.get("level")
        if level:
            parts.append(f"level={level}")
        msg = entry.get("message") or fallback
        if msg:
            parts.append(str(msg).strip())
        trial = entry.get("freeTrialPrivilege")
        if isinstance(trial, dict):
            reason = trial.get("cannotListenReason")
            if reason not in (None, "", 0):
                parts.append(f"cannotListenReason={reason}")
        return " | ".join(str(p) for p in parts if p)

    def _fetch_netease_track_url(self, song_id, is_cancelled) -> tuple[str | None, str]:
        """多链路获取网易云播放 URL，避免单接口偶发空 URL。"""
        from pyncm.apis.track import GetTrackAudio

        try:
            from pyncm.apis.track import GetTrackAudioV1
        except Exception:
            GetTrackAudioV1 = None

        try:
            from pyncm.apis.track import GetTrackDownloadURLV1
        except Exception:
            GetTrackDownloadURLV1 = None

        call_with_recover = getattr(self, "_call_with_cookie_recover", None)
        if not callable(call_with_recover):
            call_with_recover = self._safe_pyncm_call

        fallback_bitrates: list[int] = []
        for raw in _BITRATE_LADDER:
            try:
                val = int(raw)
            except (TypeError, ValueError):
                continue
            if val > 0:
                fallback_bitrates.append(val)
        if not fallback_bitrates:
            fallback_bitrates = [320000, 192000, 128000]
        fallback_bitrates = sorted(set(fallback_bitrates), reverse=True)
        last_reason = ""

        for idx, bitrate in enumerate(fallback_bitrates):
            if is_cancelled():
                return None, ""
            try:
                if idx == 0:
                    logger.info("[CloudMusic] 开始获取最高音质 URL: %sbps", bitrate)
                else:
                    logger.info("[CloudMusic] 高音质失败，降级重试: %sbps", bitrate)
                result = call_with_recover(GetTrackAudio, [song_id], bitrate=bitrate)
                entry = self._netease_audio_entry(result)
                url = str(entry.get("url") or "").strip()
                if url:
                    logger.info("[CloudMusic] 命中可用音质: %sbps", bitrate)
                    return url, ""
                last_reason = self._netease_fail_reason(entry, (result or {}).get("message", ""))
                if last_reason:
                    logger.warning("[CloudMusic] %sbps 未返回可用 URL: %s", bitrate, last_reason)
                else:
                    logger.warning("[CloudMusic] %sbps 未返回可用 URL，继续降级", bitrate)
            except Exception as e:
                last_reason = str(e)
                logger.error("[CloudMusic] 获取 %sbps URL 失败: %s", bitrate, e)

        # 兜底链路 1：V1 level 接口（部分歌曲在旧 br 接口下会返回空 URL）。
        if callable(GetTrackAudioV1):
            for level in ("hires", "lossless", "exhigh", "standard"):
                if is_cancelled():
                    return None, ""
                try:
                    logger.info("[CloudMusic] 旧接口无可用 URL，尝试 V1 音质: %s", level)
                    result = call_with_recover(GetTrackAudioV1, [song_id], level=level)
                    entry = self._netease_audio_entry(result)
                    url = str(entry.get("url") or "").strip()
                    if url:
                        logger.info("[CloudMusic] V1 命中可用音质: %s", level)
                        return url, ""
                    last_reason = self._netease_fail_reason(entry, (result or {}).get("message", ""))
                    logger.warning("[CloudMusic] V1(%s) 未返回可用 URL: %s", level, last_reason or "未知原因")
                except Exception as e:
                    last_reason = str(e)
                    logger.error("[CloudMusic] V1(%s) 获取 URL 失败: %s", level, e)

        # 兜底链路 2：下载 URL 接口（仍可能受版权限制）。
        if callable(GetTrackDownloadURLV1):
            for level in ("lossless", "exhigh", "standard"):
                if is_cancelled():
                    return None, ""
                try:
                    logger.info("[CloudMusic] 尝试下载接口兜底: %s", level)
                    result = call_with_recover(GetTrackDownloadURLV1, song_id, level=level)
                    data = result.get("data") if isinstance(result, dict) else {}
                    if isinstance(data, dict):
                        url = str(data.get("url") or "").strip()
                        if url:
                            logger.info("[CloudMusic] 下载接口命中可用 URL: %s", level)
                            return url, ""
                        msg = data.get("msg") or data.get("message") or ""
                        last_reason = str(msg or last_reason or "").strip()
                        logger.warning("[CloudMusic] 下载接口(%s) 未返回可用 URL: %s", level, last_reason or "未知原因")
                except Exception as e:
                    last_reason = str(e)
                    logger.error("[CloudMusic] 下载接口(%s) 获取 URL 失败: %s", level, e)

        return None, last_reason

    # ------------------------------------------------------------------
    # 帧事件与 seek 校对
    # ------------------------------------------------------------------

    def _on_tick(self, event: Event):
        """Tick 事件处理 - 生成音符粒子、校对 seek 偏移。

        进度发布已改为请求-响应模式：进度条每 20 tick 发送 MUSIC_PROGRESS_REQUEST，
        音乐中心通过 _on_progress_request 响应并返回进度百分比。
        """
        if self._is_playing and not self._is_paused:
            self._particle_timer += 1
            # 按 tick 间隔生成粒子，避免 frame 速率抖动影响事件节奏。
            if self._particle_timer >= _PARTICLE_INTERVAL:
                self._particle_timer = 0
                self._spawn_music_note_particles()

            # 每 N tick 在后台线程校对 seek 偏移
            self._sync_timer += 1
            if self._sync_timer >= self._sync_interval:
                self._sync_timer = 0
                self._schedule_seek_sync()
        else:
            self._particle_timer = 0
            self._sync_timer = 0

    def _schedule_seek_sync(self):
        """在后台线程中执行 seek 偏移校对，避免阻塞主线程。"""
        if not self._is_playing or self._is_paused:
            return
        future = get_compute_hub().submit_latest(
            "cloudmusic_seek_sync",
            self._sync_seek_offset,
            executor="io",
        )
        if future is None:
            return

    def _sync_seek_offset_worker(self):
        """后台线程入口：单飞执行 seek 偏移校对。"""
        try:
            self._sync_seek_offset()
        finally:
            sync_lock = getattr(self, "_seek_sync_lock", None)
            if sync_lock is not None and sync_lock.locked():
                sync_lock.release()

    def _sync_seek_offset(self):
        """后台线程：校对 seek 偏移，确保进度显示准确。"""
        try:
            import pygame

            with self._state_lock:
                if not self._is_playing or self._is_paused:
                    return
                duration_ms = self._current_duration_ms

            if duration_ms <= 0:
                duration_ms = self._get_current_duration()
                if duration_ms <= 0:
                    return

            pos_ms = pygame.mixer.music.get_pos()
            if pos_ms < 0:
                return

            real_pos_ms = pos_ms + self._seek_offset_ms

            if real_pos_ms > duration_ms + 1000:  # 允许 1 秒误差
                new_offset = max(0, duration_ms - pos_ms - 500)
                if abs(new_offset - self._seek_offset_ms) > 500:
                    logger.debug("[CloudMusic] 校对 seek 偏移: %d -> %d",
                                 self._seek_offset_ms, new_offset)
                    self._seek_offset_ms = new_offset

        except Exception as e:
            logger.debug("[CloudMusic] seek 校对失败: %s", e)

    # ------------------------------------------------------------------
    # 进度查询
    # ------------------------------------------------------------------

    def _on_progress_request(self, event: Event):
        """处理 MUSIC_PROGRESS_REQUEST 事件：返回当前播放进度百分比和剩余时间。

        进度条每 20 tick 发送此请求，音乐中心响应并返回：
        - progress: 播放进度百分比 (0.0 - 1.0)
        - remaining: 剩余时间（秒）
        """
        if not self._is_playing or self._is_paused:
            return

        try:
            import pygame
            if not pygame.mixer.music.get_busy():
                return

            duration_ms = self._current_duration_ms
            if duration_ms <= 0:
                duration_ms = self._get_current_duration()
                if duration_ms <= 0:
                    return

            pos_ms = pygame.mixer.music.get_pos()
            if pos_ms < 0:
                return

            # 如果刚 seek 过（_last_seek_progress 有效），优先使用目标进度
            if self._last_seek_progress >= 0:
                progress = self._last_seek_progress
                self._last_seek_progress = -1.0
                remaining = max(0, int((1 - progress) * duration_ms / 1000))
                target_pos_ms = int(progress * duration_ms)
                self._seek_offset_ms = target_pos_ms - pos_ms
                logger.debug("[CloudMusic] 进度请求(使用seek目标): progress=%.4f, 重新计算offset=%dms",
                             progress, self._seek_offset_ms)
            else:
                real_pos_ms = pos_ms + self._seek_offset_ms
                progress    = min(1.0, max(0.0, real_pos_ms / duration_ms))
                remaining   = max(0, int((duration_ms - real_pos_ms) / 1000))

            self._ec.publish(Event(EventType.MUSIC_PROGRESS, {
                'progress':  progress,
                'remaining': remaining,
            }))
        except Exception as e:
            logger.error("[CloudMusic] 进度请求处理失败: %s", e)

    def _get_current_duration(self) -> int:
        """获取当前播放歌曲的总时长（毫秒），无缓存时后台探测。"""
        if not self._queue or not (0 <= self._current_index < len(self._queue)):
            return 0

        song_id, _ = self._queue[self._current_index]

        if song_id in self._duration_cache:
            return self._duration_cache[song_id]

        if is_local_track_ref(song_id):
            target_path = local_track_path_from_ref(song_id)
            if target_path is None or not target_path.is_file():
                return 0
        else:
            target_path = self._find_cached_audio(song_id)
            if not target_path:
                return 0

        self._schedule_duration_probe(song_id, target_path)
        return 0

    # ------------------------------------------------------------------
    # 粒子生成
    # ------------------------------------------------------------------

    def _spawn_music_note_particles(self):
        """在音响窗口范围内生成音符粒子。"""
        self._ec.publish(Event(EventType.SPEAKER_WINDOW_REQUEST, {}))

    # ------------------------------------------------------------------
    # 播放入口
    # ------------------------------------------------------------------

    def _play_current(self):
        """播放当前队列中的歌曲（在后台线程中执行，避免阻塞主线程）。"""
        if not self._queue or self._current_index >= len(self._queue):
            logger.warning("[CloudMusic] 队列为空，无歌曲可播放")
            with self._state_lock:
                self._is_playing   = False
                self._is_paused    = False
                self._current_index = -1
            return

        song_id, display = self._queue[self._current_index]
        logger.info("[CloudMusic] 准备播放: song_id=%s, display=%s", song_id, display)
        get_compute_hub().submit_io(
            self._play_current_worker,
            song_id,
            display,
        )

    def _play_current_worker(self, song_id, display: str):
        """后台线程：执行实际的播放逻辑。"""
        if is_local_track_ref(song_id):
            local_path = local_track_path_from_ref(song_id)
            if local_path is None or not local_path.is_file():
                self._show_error("本地歌曲文件不存在，已跳过")
                self._play_next()
                return
            logger.info("[CloudMusic] 播放本地歌曲: %s", local_path)
            self._play_file(local_path, display)
            return

        cached = self._find_cached_audio(song_id)
        if cached and cached.exists():
            logger.info("[CloudMusic] 已缓存，直接播放: %s", cached)
            self._play_file(cached, display)
        else:
            logger.info("[CloudMusic] 未缓存，开始下载: song_id=%s", song_id)
            self._download_and_play(song_id, display)

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------

    def _download_and_play(self, song_id, display: str):
        """下载歌曲并播放。"""
        if self._download_thread is not None and not self._download_thread.done():
            self._download_cancel.set()

        # 每次下载使用独立取消令牌，避免复用同一个 Event 导致竞态。
        cancel_event = threading.Event()
        self._download_cancel = cancel_event

        self._show_info(f"♪ 获取中: {display or song_id}")

        self._download_thread = get_compute_hub().submit_io(
            self._download_worker,
            song_id,
            display,
            cancel_event,
        )

    def _download_worker(self, song_id, display: str, cancel_event: threading.Event):
        """后台下载线程。"""
        def _is_cancelled() -> bool:
            # 令牌被置位，或当前下载令牌已被新任务替换，都视为取消。
            return cancel_event.is_set() or cancel_event is not self._download_cancel

        if self._is_qq_track_ref(song_id):
            self._download_worker_qq(song_id, display, _is_cancelled)
            return
        if self._is_kugou_track_ref(song_id):
            self._download_worker_kugou(song_id, display, _is_cancelled)
            return

        try:
            if not self._login_ready.wait(timeout=TIMEOUTS['login_wait']):
                self._show_error("登录超时，请检查网络")
                return

            if _is_cancelled():
                return

            # 获取元信息
            from pyncm.apis.track import GetTrackDetail
            detail  = GetTrackDetail([song_id])
            song    = (detail.get("songs") or [{}])[0]
            title   = song.get("name") or str(song_id)
            artists = song.get("ar") or []
            artist  = "、".join(a["name"] for a in artists if a.get("name"))

            if _is_cancelled():
                return

            self._save_meta(song_id, {"title": title, "artist": artist})

            url, fail_reason = self._fetch_netease_track_url(song_id, _is_cancelled)
            if _is_cancelled():
                return

            if not url:
                if fail_reason:
                    logger.warning("[CloudMusic] 歌曲 URL 获取失败: song_id=%s reason=%s", song_id, fail_reason)
                self._show_error(f"无法获取歌曲 {song_id} 的播放链接")
                return

            if _is_cancelled():
                return

            cached = self._download_url_to_cache(song_id, url, _is_cancelled)
            if cached is None:
                return

            if not _is_cancelled():
                play_display = self._build_play_display(display, title, artist)
                self._play_file(cached, play_display)

        except Exception as e:
            logger.error("[CloudMusic] 下载失败: %s", e)
            self._show_error(f"歌曲下载失败: {e}")

    def _download_worker_qq(self, song_id, display: str, is_cancelled):
        """QQ 模式下载线程。"""
        mid = self._qq_mid_from_track_ref(song_id)
        if not mid:
            self._show_error("QQ歌曲标识无效")
            return

        try:
            client = get_qqmusic_provider_client()
            logged_in = bool(self.provider_logged_in("qq")) or bool(client.is_logged_in())
            if not logged_in:
                logger.info("[CloudMusic] QQ 当前未登录，继续尝试匿名取流: song_id=%s", song_id)
            media_mid = self._qq_media_mid_from_track_ref(song_id)
            detail = client.get_song_detail(song_mid=mid) or {}
            title = str(detail.get("title") or mid).strip() or mid
            artist = str(detail.get("artist") or "未知作者").strip() or "未知作者"
            if not media_mid:
                media_mid = str(detail.get("media_mid") or "").strip() or None

            if is_cancelled():
                return

            self._save_meta(song_id, {"title": title, "artist": artist})
            url = client.get_song_url(mid, media_mid=media_mid)
            if not url:
                meta = client.get_last_vkey_meta() if hasattr(client, "get_last_vkey_meta") else {}
                code = meta.get("code")
                result = meta.get("result")
                used_uin = meta.get("uin")
                tried_uins = meta.get("tried_uins")
                musickey_refresh = meta.get("musickey_refresh")
                preview_only = bool(meta.get("preview_only"))
                preview_blocked = bool(meta.get("preview_blocked"))
                preview_unavailable = bool(meta.get("preview_unavailable"))
                auth_snapshot = meta.get("auth_snapshot")
                logger.warning(
                    "[CloudMusic] QQ 取流失败 song_id=%s code=%s result=%s logged_in=%s uin=%s tried_uins=%s musickey_refresh=%s preview_only=%s preview_blocked=%s preview_unavailable=%s auth=%s",
                    song_id,
                    code,
                    result,
                    logged_in,
                    used_uin,
                    tried_uins,
                    musickey_refresh,
                    preview_only,
                    preview_blocked,
                    preview_unavailable,
                    auth_snapshot,
                )
                if preview_only and preview_blocked:
                    self._show_error("QQ当前仅返回试听资源，已拦截试听链接。请重新登录并确认会员权益后重试")
                elif preview_only and preview_unavailable:
                    self._show_error("QQ当前仅返回试听资源且链接不可用，请稍后重试或切换歌曲")
                elif str(result) == "104003" or str(code) == "1000":
                    self._show_error("QQ歌曲暂不可播放（平台要求账号权限或重新登录）")
                elif code is not None or result is not None:
                    self._show_error(f"QQ歌曲暂不可播放（code={code}, result={result}）")
                else:
                    self._show_error("QQ歌曲暂不可播放（版权或接口限制）")
                return

            if is_cancelled():
                return

            cached = self._download_url_to_cache(song_id, url, is_cancelled)
            if cached is None:
                return

            if is_cancelled():
                return

            play_display = self._build_play_display(display, title, artist)
            self._play_file(cached, play_display)
        except Exception as e:
            logger.error("[CloudMusic] QQ 下载失败: %s", e)
            self._show_error(f"QQ歌曲下载失败: {e}")

    def _download_worker_kugou(self, song_id, display: str, is_cancelled):
        """酷狗模式下载线程。"""
        song_hash = self._kugou_hash_from_track_ref(song_id)
        if not song_hash:
            self._show_error("酷狗歌曲标识无效")
            return

        try:
            client = get_kugou_provider_client()
            logged_in = bool(self.provider_logged_in("kugou")) or bool(client.is_logged_in())
            if not logged_in:
                logger.info("[CloudMusic] 酷狗当前未登录，继续尝试取流: song_id=%s", song_id)
            album_id = self._kugou_album_id_from_track_ref(song_id)
            audio_id = self._kugou_audio_id_from_track_ref(song_id)
            encode_mix = self._kugou_encode_mix_id_from_track_ref(song_id)
            detail = client.get_song_detail(
                song_hash=song_hash,
                album_id=album_id,
                album_audio_id=audio_id,
                encode_album_audio_id=encode_mix,
            ) or {}
            title = str(detail.get("title") or song_hash).strip() or song_hash
            artist = str(detail.get("artist") or "未知作者").strip() or "未知作者"

            if is_cancelled():
                return

            self._save_meta(song_id, {"title": title, "artist": artist})
            url_candidates = self._kugou_url_candidates(
                client,
                song_hash,
                album_id=album_id,
                audio_id=audio_id,
                encode_mix=encode_mix,
                detail=detail,
            )
            if not url_candidates:
                meta = client.get_last_songinfo_meta() if hasattr(client, "get_last_songinfo_meta") else {}
                status = meta.get("status")
                err_code = meta.get("err_code")
                anti_brush = bool(meta.get("anti_brush"))
                logger.warning(
                    "[CloudMusic] 酷狗取流失败 song_id=%s status=%s err_code=%s anti_brush=%s logged_in=%s",
                    song_id,
                    status,
                    err_code,
                    anti_brush,
                    logged_in,
                )
                if anti_brush or str(err_code) == "30020":
                    self._show_error("酷狗触发安全验证，请先在酷狗完成验证后重试")
                elif err_code is not None or status is not None:
                    self._show_error(f"酷狗歌曲暂不可播放（status={status}, err_code={err_code}）")
                else:
                    self._show_error("酷狗歌曲暂不可播放（版权或接口限制）")
                return

            if is_cancelled():
                return

            cached: Path | None = None
            last_download_error: Exception | None = None
            refresh_retried = False
            idx = 0
            while idx < len(url_candidates):
                if is_cancelled():
                    return
                current_url = url_candidates[idx]
                idx += 1
                try:
                    cached = self._download_url_to_cache(song_id, current_url, is_cancelled)
                    if cached is None:
                        return
                    break
                except Exception as e:
                    last_download_error = e
                    status_code = self._http_status_from_error(e)
                    host = str(urlparse(current_url).hostname or "").strip()
                    logger.warning(
                        "[CloudMusic] 酷狗下载地址失败: song_id=%s idx=%s/%s host=%s status=%s err=%s",
                        song_id,
                        idx,
                        len(url_candidates),
                        host or "<unknown>",
                        status_code,
                        e,
                    )
                    should_refresh_url = self._is_name_resolution_error(e) or status_code in (401, 403)
                    if should_refresh_url and not refresh_retried:
                        refresh_retried = True
                        refreshed_detail = client.get_song_detail(
                            song_hash=song_hash,
                            album_id=album_id,
                            album_audio_id=audio_id,
                            encode_album_audio_id=encode_mix,
                        ) or {}
                        new_urls = self._kugou_url_candidates(
                            client,
                            song_hash,
                            album_id=album_id,
                            audio_id=audio_id,
                            encode_mix=encode_mix,
                            detail=refreshed_detail,
                        )
                        appended = self._append_unique_urls(url_candidates, new_urls)
                        if appended:
                            reason = "dns" if self._is_name_resolution_error(e) else f"http_{status_code}"
                            logger.info(
                                "[CloudMusic] 酷狗下载失败后已刷新取流地址重试: song_id=%s reason=%s total=%s",
                                song_id,
                                reason,
                                len(url_candidates),
                            )
            if cached is None:
                if last_download_error is not None:
                    raise last_download_error
                return

            if is_cancelled():
                return

            play_display = self._build_play_display(display, title, artist)
            self._play_file(cached, play_display)
        except Exception as e:
            logger.error("[CloudMusic] 酷狗下载失败: %s", e)
            self._show_error(f"酷狗歌曲下载失败: {e}")

    def _download_url_to_cache(self, song_id, url: str, is_cancelled) -> Path | None:
        """下载给定 URL 到缓存并返回落盘路径。"""
        cache_dir = self._cache_dir_for_song(song_id)
        tmp = cache_dir / f"{self._cache_key(song_id)}.download.tmp"
        first_chunk = b""
        session = self._download_session_for_song(song_id)
        header_profiles = self._download_header_profiles(song_id, url)
        resp = None
        last_error: Exception | None = None
        fallback_session = None

        for idx, headers in enumerate(header_profiles, start=1):
            try:
                resp = session.get(
                    url,
                    timeout=(10, 120),
                    stream=True,
                    headers=headers,
                    allow_redirects=True,
                )
                resp.raise_for_status()
                break
            except requests.HTTPError as e:
                last_error = e
                status = self._http_status_from_error(e)
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass
                    finally:
                        resp = None
                is_retryable = status in (401, 403, 429)
                if is_retryable and idx < len(header_profiles):
                    logger.warning(
                        "[CloudMusic] 下载请求被拒绝，切换请求头重试: song_id=%s status=%s step=%s/%s url=%s",
                        song_id,
                        status,
                        idx,
                        len(header_profiles),
                        url,
                    )
                    continue
                raise
            except Exception as e:
                last_error = e
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass
                    finally:
                        resp = None
                if idx < len(header_profiles):
                    logger.warning(
                        "[CloudMusic] 下载请求失败，切换请求头重试: song_id=%s step=%s/%s url=%s err=%s",
                        song_id,
                        idx,
                        len(header_profiles),
                        url,
                        e,
                    )
                    continue
                raise

        if resp is None:
            if last_error is not None:
                # 酷狗链路兜底：使用全新无环境代理会话重试一次，规避会话/代理污染导致的 403。
                status = self._http_status_from_error(last_error)
                if self._is_kugou_track_ref(song_id) and status in (401, 403):
                    fallback_session = requests.Session()
                    fallback_session.trust_env = False
                    try:
                        resp = fallback_session.get(
                            url,
                            timeout=(10, 120),
                            stream=True,
                            headers=self._download_base_headers(),
                            allow_redirects=True,
                        )
                        resp.raise_for_status()
                        logger.info(
                            "[CloudMusic] 酷狗下载启用无环境会话兜底成功: song_id=%s status=%s url=%s",
                            song_id,
                            resp.status_code,
                            url,
                        )
                    except Exception:
                        raise last_error
                else:
                    raise last_error
            raise RuntimeError("下载失败：未获取到响应")

        try:
            with open(str(tmp), "wb") as f:
                for chunk in resp.iter_content(chunk_size=16384):
                    if is_cancelled():
                        tmp.unlink(missing_ok=True)
                        return None
                    if not chunk:
                        continue
                    if not first_chunk:
                        first_chunk = chunk
                    f.write(chunk)
        finally:
            try:
                resp.close()
            except Exception:
                pass
            try:
                if fallback_session is not None:
                    fallback_session.close()
            except Exception:
                pass

        audio_ext = self._detect_audio_ext(
            resp.url or url,
            resp.headers.get("Content-Type", ""),
            first_chunk,
        )
        cached = self._cache_path(song_id, audio_ext)
        self._cleanup_song_audio_cache(song_id, keep_path=cached)
        tmp.replace(cached)
        logger.info("[CloudMusic] 下载完成: %s (ext=%s)", cached, audio_ext)
        return cached

    # ------------------------------------------------------------------
    # pygame 播放
    # ------------------------------------------------------------------

    def _init_pygame(self):
        """在主线程中初始化 pygame mixer。"""
        try:
            import pygame
            pygame.mixer.init()
            with self._state_lock:
                self._pygame_initialized = True
            logger.info("[CloudMusic] pygame mixer 初始化完成")
        except Exception as e:
            logger.error("[CloudMusic] pygame mixer 初始化失败: %s", e)

    def _play_file(self, path: Path, display: str):
        """
        播放本地文件（通过信号调度到主线程执行）。

        由于 pygame.mixer 操作必须在主线程中执行，
        此方法会通过 Qt 信号将播放请求调度到主线程。
        """
        logger.debug("[CloudMusic] 请求播放文件: %s", path)
        self._play_signal.play_requested.emit(str(path), display)

    def _do_play_file(self, path_str: str, display: str):
        """
        在主线程中执行实际的播放操作。

        此方法由 Qt 信号调用，确保 pygame 操作在主线程中执行。
        """
        path = Path(path_str)
        logger.info("[CloudMusic] 在主线程播放文件: %s", path)

        with self._state_lock:
            pygame_ready = self._pygame_initialized
            gen          = self._play_gen

        if not pygame_ready:
            self._show_error("音频系统未就绪")
            return

        try:
            import pygame

            pygame.mixer.music.load(str(path))
            pygame.mixer.music.set_volume(get_effective_music_volume(self._volume))
            pygame.mixer.music.play()

            # 新歌曲开始，重置 seek 偏移
            self._seek_offset_ms      = 0
            self._last_seek_progress  = -1.0
            self._current_duration_ms = 0

            # 时长探测改为后台执行，避免开始播放时阻塞主线程。
            if self._queue and 0 <= self._current_index < len(self._queue):
                song_id, _ = self._queue[self._current_index]
                self._schedule_duration_probe(song_id, path)

            with self._state_lock:
                if self._play_gen != gen:
                    logger.debug("[CloudMusic] 播放被取消（代次不匹配）")
                    pygame.mixer.music.stop()
                    return
                self._is_playing = True
                self._is_paused  = False

            self._show_info(f"正在播放: {display}")
            self._publish_status()

            # 保存到播放历史（去重）
            if self._queue and 0 <= self._current_index < len(self._queue):
                song_id, title_display = self._queue[self._current_index]
                text = str(title_display or "").strip()
                m = _DISPLAY_PREFIX_RE.match(text)
                if m:
                    text = m.group(1).strip()
                if " - " in text:
                    parts = text.split(" - ", 1)
                    title = parts[0].strip()
                    artist = parts[1].strip()
                else:
                    title = text
                    artist = ""
                duration_for_history = self._duration_cache.get(song_id)
                history_provider = self._history_provider_for_song_id(song_id)
                get_music_history(history_provider).add(song_id, title, artist, duration_for_history)

            # 启动播放监控线程
            get_compute_hub().submit_io(
                self._monitor_playback,
                display,
                gen,
            )

        except Exception as e:
            logger.error("[CloudMusic] 播放失败: %s", e)
            self._show_error(f"播放失败: {e}")
            with self._state_lock:
                self._is_playing = False
                self._is_paused  = False

    # ------------------------------------------------------------------
    # 播放状态监控
    # ------------------------------------------------------------------

    def _monitor_playback(self, display: str, gen: int):
        """监控播放状态，播放完成后自动播放下一首。gen 为本次播放代次，不匹配则静默退出。"""
        try:
            import pygame
            nonbusy_streak = 0
            last_pos_ms = 0
            recover_attempts = 0
            max_recover_attempts = 2

            while True:
                with self._state_lock:
                    if self._play_gen != gen:
                        return
                    if not self._is_playing:
                        return
                    if self._is_paused:
                        pass  # 暂停状态，等待恢复

                time.sleep(0.5)

                with self._state_lock:
                    if self._play_gen != gen:
                        return
                    if self._is_paused:
                        continue
                    if not self._is_playing:
                        return

                try:
                    pos_ms = pygame.mixer.music.get_pos()
                except Exception:
                    pos_ms = -1
                if pos_ms >= 0:
                    real_pos_ms = max(0, pos_ms + self._seek_offset_ms)
                    if real_pos_ms > last_pos_ms:
                        last_pos_ms = real_pos_ms

                is_busy = bool(pygame.mixer.music.get_busy())
                if is_busy:
                    nonbusy_streak = 0
                    continue

                nonbusy_streak += 1
                duration_ms = self._current_duration_ms
                if duration_ms <= 0:
                    duration_ms = self._get_current_duration()
                near_end = duration_ms > 0 and last_pos_ms >= max(0, duration_ms - 2000)

                # 先防抖：连续两次非 busy 且接近结尾，再判定自然播放完成。
                if near_end and nonbusy_streak >= 2:
                    logger.info(
                        "[CloudMusic] 播放完成: %s (pos=%dms, duration=%dms)",
                        display,
                        last_pos_ms,
                        duration_ms,
                    )
                    with self._state_lock:
                        self._is_playing = False
                    break

                # 某些音源在试听节点会出现瞬时非 busy，先尝试原位恢复，避免误切歌。
                if (not near_end) and nonbusy_streak >= 2 and recover_attempts < max_recover_attempts:
                    recover_attempts += 1
                    resume_sec = max(0.0, last_pos_ms / 1000.0)
                    logger.warning(
                        "[CloudMusic] 检测到疑似提前停止，尝试恢复(%d/%d): %s pos=%.2fs duration=%dms",
                        recover_attempts,
                        max_recover_attempts,
                        display,
                        resume_sec,
                        duration_ms,
                    )
                    try:
                        pygame.mixer.music.play()
                        if resume_sec > 0:
                            pygame.mixer.music.set_pos(resume_sec)
                        self._seek_offset_ms = int(resume_sec * 1000)
                        nonbusy_streak = 0
                        continue
                    except Exception as e:
                        logger.warning("[CloudMusic] 恢复播放失败: %s", e)
                        try:
                            pygame.mixer.music.stop()
                        except Exception:
                            pass

                if nonbusy_streak >= 4:
                    logger.info(
                        "[CloudMusic] 播放结束(未接近结尾且恢复失败): %s (pos=%dms, duration=%dms)",
                        display,
                        last_pos_ms,
                        duration_ms,
                    )
                    with self._state_lock:
                        self._is_playing = False
                    break

            # 只有歌曲真正播放完成才会执行到这里
            with self._state_lock:
                if self._play_gen != gen:
                    return

                mode = getattr(self, "_play_mode", "list_loop")
                if mode not in ("single_loop", "list_loop", "random"):
                    mode = "list_loop"

                queue_len = len(self._queue)
                has_next = queue_len > 0

                if queue_len <= 0:
                    self._current_index = -1
                elif mode == "single_loop":
                    # 单曲循环：队列不变，继续播放当前歌曲
                    self._current_index = 0
                else:
                    # 列表循环/随机播放：已播放歌曲放到队尾，保持列表可持续播放
                    current_song = self._queue.pop(0)
                    self._queue.append(current_song)
                    if mode == "random" and len(self._queue) > 1:
                        random.shuffle(self._queue)
                    self._current_index = 0
                    has_next = True

            self._ec.publish(Event(EventType.MUSIC_SONG_END, {
                'play_mode': mode,
            }))

            if has_next:
                self._play_current()
            else:
                with self._state_lock:
                    self._is_playing = False
                    self._is_paused  = False
                self._publish_status()
                logger.info("[CloudMusic] 队列播放完成")

        except Exception as e:
            logger.error("[CloudMusic] 监控失败: %s", e)

    # ------------------------------------------------------------------
    # 下一首 / 停止
    # ------------------------------------------------------------------

    def _play_next(self):
        """播放下一首：停止当前播放，移除当前歌曲，播放队列中的下一首。"""
        logger.debug("[CloudMusic] 播放下一首")

        if self._download_thread and not self._download_thread.done():
            self._download_cancel.set()

        try:
            import pygame
            pygame.mixer.music.stop()
        except Exception:
            pass

        with self._state_lock:
            self._play_gen += 1
            if self._queue:
                removed = self._queue.pop(0)
                logger.debug("[CloudMusic] 移除歌曲: %s", removed)
            queue_len           = len(self._queue)
            self._current_index = 0 if queue_len > 0 else -1
            self._is_playing    = False
            self._is_paused     = False

        if self._queue:
            self._play_current()
        else:
            logger.info("[CloudMusic] 队列播放完成")
            self._publish_status()

    def _stop_internal(self):
        """停止当前播放（内部使用）。"""
        logger.debug("[CloudMusic] 停止播放")

        if self._download_thread and not self._download_thread.done():
            self._download_cancel.set()

        try:
            import pygame
            pygame.mixer.music.stop()
        except Exception:
            pass

        with self._state_lock:
            self._is_playing    = False
            self._is_paused     = False
            self._current_index = -1
            self._play_gen     += 1  # 使所有旧监控线程失效
        self._publish_status()

