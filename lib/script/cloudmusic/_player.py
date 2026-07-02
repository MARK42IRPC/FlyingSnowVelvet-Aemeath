"""网易云音乐 - 底层 MCI 长音乐播放器

与 VoiceCore 的区别：
  VoiceCore  — 短音效，多频道并发，不支持暂停
  MciMusicPlayer — 单曲，支持播放/暂停/恢复/停止，音量动态调整

Windows MCI 命令参考：
  open "{path}" type mpegvideo alias {alias}
  play / pause / resume / stop / close {alias}
  setaudio {alias} volume to {0-1000}
  status {alias} mode  → playing | paused | stopped
"""

import ctypes
import threading
import uuid
from typing import Callable, Optional

from lib.core.logger import get_logger

logger = get_logger(__name__)


# ── Windows MCI ─────────────────────────────────────────────────────────
_winmm = ctypes.windll.winmm
_mci_lock = threading.Lock()


def _mci(cmd: str) -> int:
    """发送 MCI 字符串命令（线程安全），返回错误码，0 表示成功。"""
    with _mci_lock:
        return _winmm.mciSendStringW(cmd, None, 0, None)


def _mci_query(cmd: str, buf_size: int = 128) -> str:
    """发送 MCI 查询命令，返回结果字符串。"""
    buf = ctypes.create_unicode_buffer(buf_size)
    with _mci_lock:
        _winmm.mciSendStringW(cmd, buf, buf_size, None)
    return buf.value


# ── 播放器 ───────────────────────────────────────────────────────────────
class MciMusicPlayer:
    """
    基于 Windows MCI 的单曲播放器，线程安全。

    状态机：
        idle ──play()──> playing ──pause()──> paused
                    ↑                             |
                    └───────resume()──────────────┘
        任意状态 ──stop()──> idle
    """

    _POLL_INTERVAL = 0.5    # 播放完成轮询间隔（秒）

    def __init__(self):
        self._alias:     Optional[str] = None
        self._state:     str           = "idle"   # idle | playing | paused
        self._lock:      threading.Lock = threading.Lock()
        self._stop_flag: threading.Event = threading.Event()
        self._on_finish: Optional[Callable] = None
        self._duration_ms: int = 0

    # ── 公开接口 ─────────────────────────────────────────────────────────

    def play(self, file_path: str, volume: float = 0.8,
             on_finish: Optional[Callable] = None) -> bool:
        """
        打开并播放文件，后台线程轮询自然结束。

        Args:
            file_path: 本地 MP3 绝对路径
            volume:    音量 0.0-1.0
            on_finish: 自然播完时在后台线程回调（非 stop() 打断时）
        Returns:
            True 表示成功打开并开始播放
        """
        with self._lock:
            self._close_locked()                               # 先停掉上一首

            alias = "cm_" + uuid.uuid4().hex[:8]
            ret = _mci(f'open "{file_path}" type MPEGAudio alias {alias}')
            logger.debug("[MciMusicPlayer] open %s: ret=%s, file=%s", alias, ret, file_path)
            if ret != 0:
                logger.debug("[MciMusicPlayer] open 失败，尝试使用 mpegvideo")
                ret = _mci(f'open "{file_path}" type mpegvideo alias {alias}')
                if ret != 0:
                    return False

            _mci(f'set {alias} time format milliseconds')
            try:
                self._duration_ms = max(0, int(_mci_query(f'status {alias} length') or 0))
            except Exception:
                self._duration_ms = 0

            vol = max(0, min(1000, int(volume * 1000)))
            ret = _mci(f'setaudio {alias} volume to {vol}')
            logger.debug("[MciMusicPlayer] setaudio %s volume to %s: ret=%s", alias, vol, ret)
            ret = _mci(f'play {alias}')
            logger.debug("[MciMusicPlayer] play %s: ret=%s", alias, ret)

            self._alias      = alias
            self._state      = "playing"
            self._on_finish  = on_finish
            stop_flag        = threading.Event()
            self._stop_flag  = stop_flag

        threading.Thread(
            target=self._poll,
            args=(alias, stop_flag),
            daemon=True,
            name="cloudmusic-poll",
        ).start()
        return True

    def pause(self) -> bool:
        """暂停，返回 True 表示状态确实改变了。"""
        with self._lock:
            if self._state != "playing":
                return False
            _mci(f'pause {self._alias}')
            self._state = "paused"
            return True

    def resume(self) -> bool:
        """继续播放，返回 True 表示状态确实改变了。"""
        with self._lock:
            if self._state != "paused":
                return False
            _mci(f'resume {self._alias}')
            self._state = "playing"
            return True

    def stop(self):
        """立即停止并释放 MCI 资源。"""
        with self._lock:
            self._close_locked()

    def set_volume(self, volume: float):
        """动态调整音量（播放或暂停状态均有效），volume 0.0-1.0。"""
        with self._lock:
            if self._alias and self._state in ("playing", "paused"):
                vol = max(0, min(1000, int(volume * 1000)))
                _mci(f'setaudio {self._alias} volume to {vol}')

    def seek(self, position_ms: int) -> bool:
        """跳转到指定毫秒位置。"""
        with self._lock:
            if not self._alias or self._state not in ("playing", "paused"):
                return False
            target = max(0, int(position_ms))
            _mci(f'seek {self._alias} to {target}')
            if self._state == "playing":
                _mci(f'play {self._alias} from {target}')
            else:
                _mci(f'play {self._alias} from {target}')
                _mci(f'pause {self._alias}')
            return True

    def position_ms(self) -> int:
        """返回当前播放位置（毫秒）。"""
        with self._lock:
            if not self._alias or self._state == "idle":
                return 0
            alias = self._alias
        try:
            return max(0, int(_mci_query(f'status {alias} position') or 0))
        except Exception:
            return 0

    def duration_ms(self) -> int:
        """返回当前媒体时长（毫秒）。"""
        with self._lock:
            if self._duration_ms > 0:
                return self._duration_ms
            alias = self._alias
        if not alias:
            return 0
        try:
            value = max(0, int(_mci_query(f'status {alias} length') or 0))
        except Exception:
            value = 0
        with self._lock:
            if value > 0:
                self._duration_ms = value
        return value

    def is_busy(self) -> bool:
        with self._lock:
            return self._state in ("playing", "paused")

    @property
    def state(self) -> str:
        """当前状态：'idle' | 'playing' | 'paused'"""
        return self._state

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _close_locked(self):
        """停止并关闭 MCI（必须在 _lock 内调用）。"""
        self._stop_flag.set()
        if self._alias:
            ret = _mci(f'stop {self._alias}')
            logger.debug("[MciMusicPlayer] _close_locked stop %s: ret=%s", self._alias, ret)
            ret = _mci(f'close {self._alias}')
            logger.debug("[MciMusicPlayer] _close_locked close %s: ret=%s", self._alias, ret)
            self._alias = None
        self._state     = "idle"
        self._on_finish = None
        self._duration_ms = 0

    def _poll(self, alias: str, stop_flag: threading.Event):
        """后台轮询线程：等待 MCI 自然播完后回调 on_finish。"""
        poll_count = 0
        while not stop_flag.is_set():
            mode = _mci_query(f'status {alias} mode')
            poll_count += 1
            logger.debug("[MciMusicPlayer] _poll %s: poll_count=%s, mode=%s", alias, poll_count, mode)
            if mode in ('stopped', ''):
                logger.debug("[MciMusicPlayer] _poll %s: 播放完成（mode=%s）", alias, mode)
                break
            stop_flag.wait(self._POLL_INTERVAL)

        if stop_flag.is_set():
            logger.debug("[MciMusicPlayer] _poll %s: 被停止", alias)
            return   # 被 stop() 触发，资源已由调用方清理

        # 自然播完：清理资源 + 触发回调
        cb = None
        with self._lock:
            if self._alias == alias:     # 确认仍是同一首（未被切歌）
                _mci(f'close {alias}')
                self._alias     = None
                self._state     = "idle"
                cb              = self._on_finish
                self._on_finish = None

        logger.debug("[MciMusicPlayer] _poll %s: 触发回调", alias)
        if cb:
            try:
                cb()
            except Exception as e:
                logger.error("[MciMusicPlayer] _poll %s: 回调异常: %s", alias, e)
