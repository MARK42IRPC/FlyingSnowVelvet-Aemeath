"""网易云音乐管理器 - 使用项目事件驱动架构重写

运行时入口：
    from lib.script.music import get_music_service

    mgr = get_music_service().initialize()
    # 左键：置顶播放（插入队列首位，立即切歌）
    # 右键：加入队列末尾
    # 暂停/继续/停止通过命令处理

队列语义：
  queue[0]   = 当前正在播放/加载的歌曲（顶部）
  queue[1:]  = 等待播放

缓存目录：C:\\AemeathDeskPet\\cache\\music\\
  {song_id}.{ext}         音频文件（按真实格式保存，支持 mp3/flac/wav/m4a/aac/ogg/opus/webm）
  {song_id}.meta.json     歌曲元信息（title、artist）
"""

import threading
from concurrent.futures import Future
from typing import Optional

from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.logger import get_logger
from config.config import CLOUD_MUSIC
from config.audio import get_effective_music_volume
from config.music import get_music_history, get_volume_config

from ._constants import (
    _CACHE_DIR,
    _KUGOU_LOGIN_CACHE_FILE,
    _LEGACY_LOGIN_CACHE_FILE,
    _LOGIN_CACHE_FILE,
    _QQ_LOGIN_CACHE_FILE,
    ensure_user_storage_layout,
)
from ._provider_clients import get_kugou_provider_client, get_qqmusic_provider_client
from ._mixin_login import _LoginMixin
from ._mixin_cache import _CacheMixin
from ._mixin_playback import _PlaybackMixin
from ._mixin_events import _EventsMixin
from ._player import MciMusicPlayer
from ._qt_player import QtMusicPlayer

logger = get_logger(__name__)

_PLAY_MODE_ORDER = ("single_loop", "list_loop", "random")
_PLAY_MODE_LABELS = {
    "single_loop": "单曲循环",
    "list_loop": "列表循环",
    "random": "随机播放",
}
_DEFAULT_PROVIDER = "netease"
_KNOWN_PROVIDERS = ("netease", "qq", "kugou")


def get_cloud_music_manager() -> "CloudMusicManager":
    """兼容入口：音乐运行时单例由 MusicService 持有。"""
    from lib.script.music import get_music_service

    return get_music_service().initialize()


def cleanup_cloud_music_manager():
    """兼容入口：释放 MusicService 持有的音乐运行时。"""
    from lib.script.music.service import _instance as music_service_instance

    if music_service_instance is not None:
        music_service_instance.cleanup_backend()

_HISTORY_CLEAR_PROVIDERS = ("netease", "qq", "kugou", "local", "other")


def _iter_login_cache_files():
    files = [
        _LOGIN_CACHE_FILE,
        _QQ_LOGIN_CACHE_FILE,
        _KUGOU_LOGIN_CACHE_FILE,
        _LEGACY_LOGIN_CACHE_FILE,
    ]
    try:
        from config.shared_storage import get_shared_config_path

        files.extend([
            get_shared_config_path("music", "cloudmusic_login_cache.json"),
            get_shared_config_path("music", "qqmusic_login_cache.json"),
            get_shared_config_path("music", "kugou_login_cache.json"),
        ])
    except Exception:
        pass

    unique_files = []
    seen: set[str] = set()
    for file_path in files:
        try:
            key = str(file_path.resolve())
        except Exception:
            key = str(file_path)
        if key in seen:
            continue
        seen.add(key)
        unique_files.append(file_path)
    return unique_files


def _clear_runtime_netease_login_cookies() -> bool:
    try:
        from pyncm.apis.login import GetCurrentSession
    except Exception:
        return False

    try:
        session = GetCurrentSession()
    except Exception as e:
        logger.debug("[CloudMusic] 获取当前会话失败，无法清理网易 Cookie: %s", e)
        return False

    jar = getattr(session, "cookies", None)
    if jar is None:
        return False

    try:
        jar.clear()
        return True
    except Exception:
        pass

    cleared = False
    try:
        for cookie in list(jar):
            try:
                jar.clear(domain=cookie.domain, path=cookie.path, name=cookie.name)
                cleared = True
            except Exception:
                continue
    except Exception as e:
        logger.debug("[CloudMusic] 网易 Cookie 逐项清理失败: %s", e)
    return cleared


def _clear_music_history_data() -> dict[str, int]:
    stats = {
        "history_items": 0,
        "history_platforms": 0,
        "history_failures": 0,
    }
    for provider in _HISTORY_CLEAR_PROVIDERS:
        try:
            history = get_music_history(provider)
            stats["history_items"] += len(history.get_all())
            history.clear()
            stats["history_platforms"] += 1
        except Exception as e:
            stats["history_failures"] += 1
            logger.warning("[CloudMusic] 清理历史失败 provider=%s: %s", provider, e)
    return stats


def _clear_music_login_data(runtime_manager=None) -> dict[str, int]:
    stats = {
        "logged_in_providers": 0,
        "deleted_login_files": 0,
        "failed_login_files": 0,
        "login_provider_failures": 0,
    }

    if runtime_manager is not None:
        stats["logged_in_providers"] = sum(
            1 for provider in _KNOWN_PROVIDERS if runtime_manager.provider_logged_in(provider)
        )
        runtime_manager._qr_login_cancel.set()
        runtime_manager._publish_qr_hide()

    _clear_runtime_netease_login_cookies()

    for provider_name, getter in (("qq", get_qqmusic_provider_client), ("kugou", get_kugou_provider_client)):
        try:
            getter().set_cookies({})
        except Exception as e:
            stats["login_provider_failures"] += 1
            logger.warning("[CloudMusic] 清理 %s 登录态失败: %s", provider_name, e)

    for cache_file in _iter_login_cache_files():
        if not cache_file.exists():
            continue
        try:
            cache_file.unlink()
            stats["deleted_login_files"] += 1
        except OSError as e:
            stats["failed_login_files"] += 1
            logger.warning("[CloudMusic] 清理登录缓存失败: %s (%s)", cache_file, e)

    if runtime_manager is not None:
        try:
            runtime_manager._anonymous_login()
        except ImportError:
            runtime_manager._set_login_state(False, {}, provider="netease")
        except Exception as e:
            logger.warning("[CloudMusic] 清理后回退匿名登录失败: %s", e)
            runtime_manager._set_login_state(False, {}, provider="netease")

        with runtime_manager._state_lock:
            for provider in _KNOWN_PROVIDERS:
                runtime_manager._login_states[provider] = {"logged_in": False, "profile": {}}
            runtime_manager._sync_current_login_state_locked()
        runtime_manager._publish_login_status()

    return stats


def clear_all_history_and_login_data() -> dict[str, int]:
    """兼容入口：通过 MusicService 清空所有平台音乐历史与登录数据。"""
    from lib.script.music import clear_all_history_and_login_data as clear_via_music_service

    return clear_via_music_service()




# ── 管理器主类 ────────────────────────────────────────────────────────────
class CloudMusicManager(_LoginMixin, _CacheMixin, _PlaybackMixin, _EventsMixin):
    """
    网易云音乐播放管理器（单例）。

    使用项目事件驱动架构：
    - 订阅 MUSIC_PLAY_TOP 和 MUSIC_ENQUEUE 事件
    - 通过 INFORMATION 事件发布播放状态
    - 使用后台线程处理下载，避免阻塞主线程
    """

    def __init__(self):
        ensure_user_storage_layout()
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # 检查缓存大小，超过 500MB 时清理
        self._check_and_clean_cache()

        self._ec = get_event_center()

        # 播放状态
        self._queue          = []   # [(song_id, title), ...]
        self._current_index  = -1   # 当前播放的歌曲索引
        self._is_playing     = False
        self._is_paused      = False
        self._play_mode      = "list_loop"
        # 从配置文件读取用户保存的音量偏好
        self._volume = get_volume_config().get_volume()
        self._play_gen = 0  # 播放代次：每次 _stop_internal 递增，使僵尸监控线程失效

        # 状态锁：保护多线程访问的状态变量
        self._state_lock = threading.Lock()

        # 下载线程控制
        self._download_thread    = None
        self._download_cancel    = threading.Event()

        # 登录状态
        self._login_states: dict[str, dict] = {
            name: {"logged_in": False, "profile": {}}
            for name in _KNOWN_PROVIDERS
        }
        # 兼容旧字段：始终镜像“当前 provider”登录态
        self._is_logged_in   = False
        self._qr_login_thread: Future | None = None
        self._qr_login_cancel = threading.Event()

        # pyncm 登录
        self._login_ready = threading.Event()
        get_compute_hub().submit_io(self._login)

        self._music_player = QtMusicPlayer()
        self._connect_music_player()
        self._pending_play_display = ""
        self._pending_play_path = ""
        self._fallback_player = MciMusicPlayer()
        self._use_qt_player = True

        # 音符粒子控制
        self._particle_timer = 0   # 粒子生成计时器（帧数）

        # 时长缓存：避免每次请求进度时重新加载文件
        self._duration_cache:     dict = {}   # {song_id: duration_ms}
        self._current_duration_ms: int = 0    # 当前歌曲的缓存时长

        # 订阅事件（cleanup 中必须逐一对称解绑）
        self._subscriptions = [
            (EventType.MUSIC_PLAY_TOP,             self._on_play_top),
            (EventType.MUSIC_ENQUEUE,              self._on_enqueue),
            (EventType.MUSIC_ENQUEUE_HISTORY,      self._on_enqueue_history),
            (EventType.MUSIC_ENQUEUE_LIKED,        self._on_enqueue_liked),
            (EventType.MUSIC_ENQUEUE_LOCAL,        self._on_enqueue_local),
            (EventType.MUSIC_PLAY_QUEUE_INDEX,     self._on_play_queue_index),
            (EventType.MUSIC_PLAY_MODE_TOGGLE,     self._on_play_mode_toggle),
            (EventType.MUSIC_PLAY_PAUSE,           self._on_play_pause),
            (EventType.MUSIC_NEXT_TRACK,           self._on_next_track),
            (EventType.MUSIC_VOLUME,               self._on_volume),
            (EventType.MUSIC_SEEK,                 self._on_seek),
            (EventType.MUSIC_LOGIN_REQUEST,        self._on_login_request),
            (EventType.MUSIC_LOGIN_CANCEL_REQUEST, self._on_login_cancel_request),
            (EventType.MUSIC_LOGOUT_REQUEST,       self._on_logout_request),
            (EventType.TICK,                       self._on_tick),
            (EventType.MUSIC_PROGRESS_REQUEST,     self._on_progress_request),
            (EventType.SPEAKER_WINDOW_RESPONSE,    self._on_speaker_window_response),
        ]
        self._subscribe_all_events()

        self._publish_login_status()
        logger.info("[CloudMusic] 已初始化")

    # ------------------------------------------------------------------
    # 状态广播
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_provider_name(provider: str | None, fallback: str = _DEFAULT_PROVIDER) -> str:
        target = str(provider or fallback).strip().lower()
        return target if target in _KNOWN_PROVIDERS else fallback

    @staticmethod
    def _current_provider_name() -> str:
        return CloudMusicManager._normalize_provider_name(CLOUD_MUSIC.get("provider"), fallback=_DEFAULT_PROVIDER)

    def _sync_current_login_state_locked(self) -> tuple[str, bool, dict]:
        provider = self._current_provider_name()
        state = self._login_states.get(provider) or {}
        logged_in = bool(state.get("logged_in", False))
        profile = dict(state.get("profile") or {}) if logged_in else {}
        self._is_logged_in = logged_in
        return provider, logged_in, profile

    def _publish_status(self):
        """发布播放状态变化事件。"""
        with self._state_lock:
            is_playing = self._is_playing
            is_paused  = self._is_paused
            play_mode  = self._play_mode
        self._ec.publish(Event(EventType.MUSIC_STATUS_CHANGE, {
            'playing': is_playing and not is_paused,
            'paused':  is_paused,
            'play_mode': play_mode,
            'play_mode_label': _PLAY_MODE_LABELS.get(play_mode, _PLAY_MODE_LABELS['list_loop']),
        }))

    def _publish_login_status(self):
        """发布登录状态变化事件。"""
        with self._state_lock:
            provider, logged_in, profile = self._sync_current_login_state_locked()
            all_logged_in = {
                name: bool((self._login_states.get(name) or {}).get("logged_in", False))
                for name in _KNOWN_PROVIDERS
            }
        self._ec.publish(Event(EventType.MUSIC_LOGIN_STATUS_CHANGE, {
            'provider': provider,
            'logged_in': logged_in,
            'nickname':  self._extract_nickname(profile),
            'profile':   profile,
            'all_logged_in': all_logged_in,
        }))

    def _publish_brief_info(self, text: str) -> None:
        self._ec.publish(Event(EventType.INFORMATION, {
            "text": text,
            "min": 0,
            "max": 30,
        }))

    def _connect_music_player(self) -> None:
        self._disconnect_music_player()
        self._music_player.playback_started.connect(self._on_player_started)
        self._music_player.playback_finished.connect(self._on_player_finished)
        self._music_player.playback_error.connect(self._on_player_error)
        self._music_player.duration_changed.connect(self._on_player_duration_changed)

    def _disconnect_music_player(self) -> None:
        player = getattr(self, "_music_player", None)
        if player is None:
            return
        for signal, slot in (
            (player.playback_started, self._on_player_started),
            (player.playback_finished, self._on_player_finished),
            (player.playback_error, self._on_player_error),
            (player.duration_changed, self._on_player_duration_changed),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def _subscribe_all_events(self) -> None:
        for event_type, handler in self._subscriptions:
            self._ec.subscribe(event_type, handler)

    def _unsubscribe_all_events(self) -> None:
        for event_type, handler in getattr(self, '_subscriptions', []):
            self._ec.unsubscribe(event_type, handler)
        self._subscriptions = []

    def _set_login_state(self, logged_in: bool, profile: dict | None = None, provider: str | None = None):
        """更新登录状态并广播。"""
        fallback = self._current_provider_name() if not provider else _DEFAULT_PROVIDER
        target = self._normalize_provider_name(provider, fallback=fallback)
        with self._state_lock:
            self._login_states[target] = {
                "logged_in": bool(logged_in),
                "profile": dict(profile or {}) if logged_in else {},
            }
            self._sync_current_login_state_locked()
        self._publish_login_status()

    def provider_logged_in(self, provider: str) -> bool:
        target = self._normalize_provider_name(provider, fallback="")
        if target not in self._login_states:
            return False
        with self._state_lock:
            return bool((self._login_states.get(target) or {}).get("logged_in", False))

    def refresh_login_status(self) -> None:
        self._publish_login_status()

    @staticmethod
    def _extract_nickname(profile: dict | None) -> str:
        if not isinstance(profile, dict):
            return ''
        return str(profile.get('nickname') or profile.get('nicknameStr') or '').strip()

    def _clear_queue_locked(self) -> None:
        self._queue.clear()
        self._current_index = -1

    def _editable_queue_move_target_locked(self, index: int, direction: int) -> int:
        size = len(self._queue)
        if not (0 <= index < size):
            return -1

        target = index + direction
        if not (0 <= target < size):
            return -1

        current = self._current_index
        if 0 <= current < size and (index == current or target == current):
            return -1
        return target

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def stop(self):
        """停止播放并清空队列。"""
        self._stop_internal()
        with self._state_lock:
            self._clear_queue_locked()
        self._publish_status()
        self._show_info("⏹ 已停止")

    def clear_queue(self):
        """清空播放队列并停止播放。"""
        self._stop_internal()
        with self._state_lock:
            self._clear_queue_locked()
        self._publish_status()
        self._show_info("已清空列表")

    def pause(self):
        """暂停播放。"""
        with self._state_lock:
            if not self._is_playing or self._is_paused:
                return
        try:
            if self._use_qt_player:
                self._music_player.pause_requested.emit()
            else:
                self._fallback_player.pause()
            with self._state_lock:
                self._is_paused = True
            self._publish_brief_info("已暂停")
            self._publish_status()
        except Exception as e:
            logger.error("[CloudMusic] 暂停失败: %s", e)

    def _resume(self):
        """继续播放（内部方法）。"""
        with self._state_lock:
            if not self._is_paused:
                return
        try:
            if self._use_qt_player:
                self._music_player.resume_requested.emit()
            else:
                self._fallback_player.resume()
            with self._state_lock:
                self._is_paused  = False
                self._is_playing = True
            self._publish_brief_info("继续播放")
            self._publish_status()
        except Exception as e:
            logger.error("[CloudMusic] 继续播放失败: %s", e)

    def next_track(self):
        """播放下一首。"""
        self._play_next()

    def remove_current_queue_item(self) -> bool:
        """移除当前播放项并切到下一首；若队列已空则停止。"""
        with self._state_lock:
            if not self._queue:
                return False
        self._play_next()
        return True

    def set_volume(self, volume: float):
        """设置音量 0.0-1.0，并保存到配置文件。"""
        self._volume = max(0.0, min(1.0, volume))
        try:
            effective = get_effective_music_volume(self._volume)
            if self._use_qt_player:
                self._music_player.volume_requested.emit(effective)
            else:
                self._fallback_player.set_volume(effective)
        except Exception:
            pass
        get_volume_config().set_volume(self._volume)

    def cycle_play_mode(self) -> str:
        """循环切换播放模式并广播状态。"""
        with self._state_lock:
            current = self._play_mode
            try:
                idx = _PLAY_MODE_ORDER.index(current)
            except ValueError:
                idx = 0
            next_mode = _PLAY_MODE_ORDER[(idx + 1) % len(_PLAY_MODE_ORDER)]
            self._play_mode = next_mode
            label = _PLAY_MODE_LABELS.get(next_mode, _PLAY_MODE_LABELS['list_loop'])

        self._show_info(f"播放模式：{label}")
        self._publish_status()
        return next_mode

    def move_queue_item(self, index: int, direction: int) -> int:
        """
        单步移动队列中的歌曲（交换相邻项）。

        Args:
            index:     源索引。
            direction: -1 表示上移，+1 表示下移。

        Returns:
            移动后的新索引；返回 -1 表示移动失败。
        """
        if direction not in (-1, 1):
            return -1

        with self._state_lock:
            target = self._editable_queue_move_target_locked(index, direction)
            if target < 0:
                return -1

            self._queue[index], self._queue[target] = self._queue[target], self._queue[index]

        self._publish_status()
        return target

    def remove_queue_item(self, index: int) -> bool:
        """
        删除队列中的非当前歌曲。

        Args:
            index: 要删除的队列索引。

        Returns:
            True 表示删除成功；False 表示失败（越界或命中当前播放项）。
        """
        with self._state_lock:
            size = len(self._queue)
            if not (0 <= index < size):
                return False

            current = self._current_index
            if 0 <= current < size and index == current:
                return False

            removed = self._queue.pop(index)
            if 0 <= current < size and index < current:
                self._current_index = current - 1
            elif not self._queue:
                self._current_index = -1

        logger.info("[CloudMusic] 已删除队列歌曲: index=%d, item=%s", index, removed)
        self._publish_status()
        return True

    @staticmethod
    def _history_provider_for_song_id(song_id) -> str:
        if isinstance(song_id, int):
            return "netease"
        if isinstance(song_id, str):
            text = song_id.strip().lower()
            if text.startswith("qq:"):
                return "qq"
            if text.startswith("kugou:"):
                return "kugou"
            if text.startswith("local::"):
                return "local"
            if text.startswith("netease:"):
                return "netease"
        return "other"

    def remove_song_from_history(self, song_id) -> bool:
        """
        从历史记录中删除指定歌曲。

        Args:
            song_id: 歌曲 ID

        Returns:
            True 表示历史中存在并已删除；False 表示未命中或删除失败。
        """
        from config.music import get_music_history
        try:
            provider = self._history_provider_for_song_id(song_id)
            removed = get_music_history(provider).remove(song_id)
            if removed:
                logger.info("[CloudMusic] 已从历史移除歌曲: provider=%s song_id=%s", provider, song_id)
            return removed
        except Exception as e:
            logger.error("[CloudMusic] 删除历史歌曲失败 song_id=%s: %s", song_id, e)
            return False

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def is_playing(self) -> bool:
        with self._state_lock:
            return self._is_playing

    @property
    def is_paused(self) -> bool:
        with self._state_lock:
            return self._is_paused

    @property
    def is_logged_in(self) -> bool:
        with self._state_lock:
            _, logged_in, _ = self._sync_current_login_state_locked()
            return logged_in

    @property
    def login_nickname(self) -> str:
        with self._state_lock:
            _, _, profile = self._sync_current_login_state_locked()
        return self._extract_nickname(profile)

    @property
    def queue(self) -> list:
        """返回播放队列快照 [(song_id, display), ...]，线程安全。"""
        with self._state_lock:
            return list(self._queue)

    @property
    def play_mode(self) -> str:
        with self._state_lock:
            return self._play_mode

    @property
    def current_index(self) -> int:
        """返回当前播放歌曲在队列中的索引（-1 表示未播放）。"""
        with self._state_lock:
            return self._current_index

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def cleanup(self):
        """清理资源。"""
        # 先解绑事件，避免 cleanup 过程中继续收到回调
        self._unsubscribe_all_events()

        # 请求后台线程尽快退出
        self._download_cancel.set()
        self._qr_login_cancel.set()
        self._publish_qr_hide()

        self._stop_internal()
        self._disconnect_music_player()
        try:
            self._fallback_player.stop()
        except Exception:
            pass
        with self._state_lock:
            self._clear_queue_locked()
        self._duration_cache.clear()
        self._current_duration_ms = 0
        try:
            self._music_player.deleteLater()
        except Exception:
            pass

        logger.info("[CloudMusic] 已清理")
