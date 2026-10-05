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
from pathlib import Path
from typing import Callable, Optional

from lib.core.logger import get_logger
from ._constants import local_audio_needs_decode
from ._decoder import ensure_decoded_wav

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

    实现 ``lib.core.services.music_playback.MusicPlayerProtocol``：命令是异步的，
    结果（开始播放 / 自然播完 / 失败 / 时长）通过 ``set_callbacks()`` 注册的回调汇报，
    回调在调用线程或轮询线程上执行，订阅方需自行保证线程安全。

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
        self._duration_ms: int = 0
        self._generation:  int = 0
        self._callbacks: dict = {
            "started":  None,
            "finished": None,
            "error":    None,
            "duration": None,
        }

    # ── 回调 ─────────────────────────────────────────────────────────────

    def set_callbacks(
        self,
        *,
        on_started:  Optional[Callable] = None,
        on_finished: Optional[Callable] = None,
        on_error:    Optional[Callable] = None,
        on_duration_changed: Optional[Callable] = None,
    ) -> None:
        """注册回调，整体替换上一组；全部传 None 即解绑。"""
        with self._lock:
            self._callbacks = {
                "started":  on_started,
                "finished": on_finished,
                "error":    on_error,
                "duration": on_duration_changed,
            }

    def _emit(self, name: str, *args) -> None:
        """取出回调并在锁外调用，避免订阅方回查播放器时自锁。"""
        with self._lock:
            callback = self._callbacks.get(name)
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as e:
            logger.error("[MciMusicPlayer] %s 回调异常: %s", name, e)

    # ── 公开接口 ─────────────────────────────────────────────────────────

    def play(self, file_path: str, volume: float = 0.8, generation: int = 0) -> None:
        """
        打开并播放文件，后台线程轮询自然结束。

        Args:
            file_path:  本地音频绝对路径
            volume:     音量 0.0-1.0
            generation: 播放代数，回调原样带回，供订阅方丢弃过期结果
        """
        source = Path(file_path)
        play_path = str(source)
        if local_audio_needs_decode(source):
            decoded = ensure_decoded_wav(source)
            if decoded is None:
                self._emit(
                    "error",
                    int(generation),
                    f"本地音乐解码失败（缺少解码器或文件损坏）: {source.name}",
                )
                return
            play_path = str(decoded)

        failed = False
        with self._lock:
            self._close_locked()                               # 先停掉上一首

            alias = "cm_" + uuid.uuid4().hex[:8]
            ret = _mci(f'open "{play_path}" type mpegvideo alias {alias}')
            logger.debug("[MciMusicPlayer] open %s: ret=%s, file=%s", alias, ret, play_path)
            if ret != 0:
                logger.debug("[MciMusicPlayer] mpegvideo 打开失败，尝试不带 type")
                ret = _mci(f'open "{play_path}" alias {alias}')
            if ret != 0:
                failed = True
            else:
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
                self._generation = int(generation)
                stop_flag        = threading.Event()
                self._stop_flag  = stop_flag

        if failed:
            self._emit("error", int(generation), f"MCI 无法打开音频文件: {play_path}")
            return

        threading.Thread(
            target=self._poll,
            args=(alias, stop_flag),
            daemon=True,
            name="cloudmusic-poll",
        ).start()

        self._emit("started", int(generation))
        duration_ms = self.duration_ms()
        if duration_ms > 0:
            self._emit("duration", int(generation), duration_ms)

    def pause(self) -> bool:
        """暂停，返回 True 表示状态确实改变了。"""
        with self._lock:
            if self._state != "playing":
                return False
            _mci(f'pause {self._alias}')
            self._state = "paused"
            return True

    def resume(self) -> bool:
        """恢复播放，返回 True 表示状态确实改变了。"""
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

    def cleanup(self) -> None:
        """停止播放并解绑回调；可重复调用。"""
        self.stop()
        self.set_callbacks()

    def set_volume(self, volume: float):
        """动态调整音量（播放或暂停状态都有效），volume 0.0-1.0。"""
        with self._lock:
            if self._alias and self._state in ("playing", "paused"):
                vol = max(0, min(1000, int(volume * 1000)))
                _mci(f'setaudio {self._alias} volume to {vol}')

    def seek(self, position_ms: int) -> bool:
        """跳转到指定播放位置。"""
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
        """停止并关闭 MCI；必须在 _lock 内调用。"""
        self._stop_flag.set()
        if self._alias:
            ret = _mci(f'stop {self._alias}')
            logger.debug("[MciMusicPlayer] _close_locked stop %s: ret=%s", self._alias, ret)
            ret = _mci(f'close {self._alias}')
            logger.debug("[MciMusicPlayer] _close_locked close %s: ret=%s", self._alias, ret)
            self._alias = None
        self._state     = "idle"
        self._duration_ms = 0

    def _poll(self, alias: str, stop_flag: threading.Event):
        """后台轮询线程：等待 MCI 自然结束并回调 on_finished。"""
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
            return   # 由 stop() 负责释放资源，不再回调

        # 自然放完：释放资源 + 触发回调
        generation = 0
        with self._lock:
            if self._alias == alias:     # 确认还是同一首，未被打断
                _mci(f'close {alias}')
                self._alias     = None
                self._state     = "idle"
                generation      = self._generation

        logger.debug("[MciMusicPlayer] _poll %s: 触发回调", alias)
        self._emit("finished", generation)
