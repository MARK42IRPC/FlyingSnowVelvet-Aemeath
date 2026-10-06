"""网易云音乐 - 底层 MCI 长音乐播放器

与 VoiceCore 的区别：
  VoiceCore  — 短音效，多频道并发，不支持暂停
  MciMusicPlayer — 单曲，支持播放/暂停/恢复/停止，音量动态调整

Windows MCI 命令参考：
  open "{path}" type mpegvideo alias {alias}
  play / pause / resume / stop / close {alias}
  setaudio {alias} volume to {0-1000}
  status {alias} mode  → playing | paused | stopped

线程亲和（本模块的核心约束）：MCI 别名由哪个线程 ``open``，就只有那个线程能对它发
``play`` / ``status`` / ``setaudio`` / ``close``。在别的线程上执行会返回错误码 263
（"指定的设备未打开，或不被 MCI 所识别"），查询结果为空串。本模块因此用一个常驻工作
线程独占该播放器的全部 MCI 调用：命令投递过去并等待完成，查询读工作线程维护的缓存状态。
回调在工作线程上触发，订阅方需自行保证线程安全。
"""

import ctypes
import queue
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
    """发送 MCI 查询命令，返回结果字符串（失败时为空串）。"""
    buf = ctypes.create_unicode_buffer(buf_size)
    with _mci_lock:
        _winmm.mciSendStringW(cmd, buf, buf_size, None)
    return buf.value


def _mci_query_result(cmd: str, buf_size: int = 128) -> tuple[int, str]:
    """发送 MCI 查询命令，同时返回错误码与结果字符串。

    只返回字符串的 ``_mci_query`` 无法区分"真的播完了"和"这个线程不能操作该别名"
    （两者都得到空串）。轮询播放状态必须看错误码，否则跨线程失败会被误判成播完。
    """
    buf = ctypes.create_unicode_buffer(buf_size)
    with _mci_lock:
        ret = _winmm.mciSendStringW(cmd, buf, buf_size, None)
    return ret, buf.value


class _Request:
    """一次投递到工作线程的 MCI 操作。"""

    __slots__ = ("kind", "payload", "done", "result")

    def __init__(self, kind: str, payload=None) -> None:
        self.kind = kind
        self.payload = payload
        self.done = threading.Event()
        self.result = None


# ── 播放器 ───────────────────────────────────────────────────────────────
class MciMusicPlayer:
    """
    基于 Windows MCI 的单曲播放器，线程安全。

    实现 ``lib.core.services.music_playback.MusicPlayerProtocol``：命令是异步的，
    结果（开始播放 / 自然播完 / 失败 / 时长）通过 ``set_callbacks()`` 注册的回调汇报。
    全部 MCI 调用都在一个常驻工作线程上执行（见模块说明的线程亲和），因此
    ``pause`` / ``resume`` / ``stop`` / ``set_volume`` / ``seek`` 会投递到该线程并等待，
    ``position_ms`` / ``duration_ms`` / ``is_busy`` 则立即返回工作线程维护的缓存值。

    状态机：
        idle ──play()──> playing ──pause()──> paused
                    ↑                             │
                    └───────resume()──────────────┘
        任意状态 ──stop()──> idle
    """

    _TICK_INTERVAL = 0.25     # 工作线程轮询播放状态 / 位置的间隔（秒）
    _REQUEST_TIMEOUT = 10.0   # 等待工作线程完成一次命令的上限（秒）

    def __init__(self):
        self._alias:     Optional[str] = None
        self._state:     str           = "idle"   # idle | playing | paused
        self._lock:      threading.Lock = threading.Lock()
        self._duration_ms: int = 0
        self._position_ms: int = 0
        self._generation:  int = 0
        self._closed:    threading.Event = threading.Event()
        self._callbacks: dict = {
            "started":  None,
            "finished": None,
            "error":    None,
            "duration": None,
        }
        self._requests: "queue.Queue" = queue.Queue()
        self._worker = threading.Thread(
            target=self._run,
            name="cloudmusic-mci",
            daemon=True,
        )
        self._worker.start()

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
        打开并播放文件，由工作线程负责自然结束的轮询。

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

        info = self._post("play", (play_path, float(volume), int(generation)))
        if not info:
            self._emit("error", int(generation), f"MCI 无法打开音频文件: {play_path}")
            return

        self._emit("started", int(generation))
        duration_ms = int(info.get("duration_ms") or 0)
        if duration_ms > 0:
            self._emit("duration", int(generation), duration_ms)

    def pause(self) -> bool:
        """暂停，返回 True 表示状态确实改变了。"""
        return bool(self._post("pause"))

    def resume(self) -> bool:
        """恢复播放，返回 True 表示状态确实改变了。"""
        return bool(self._post("resume"))

    def stop(self):
        """立即停止并释放 MCI 资源。"""
        self._post("stop")

    def cleanup(self) -> None:
        """停止播放、解绑回调并结束工作线程；可重复调用。"""
        try:
            self._post("stop")
        finally:
            self.set_callbacks()
        self._closed.set()
        self._post("quit", wait=False)
        worker = self._worker
        if worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=2.0)

    def set_volume(self, volume: float):
        """动态调整音量（播放或暂停状态都有效），volume 0.0-1.0。"""
        self._post("volume", max(0.0, min(1.0, float(volume))))

    def seek(self, position_ms: int) -> bool:
        """跳转到指定播放位置。"""
        return bool(self._post("seek", max(0, int(position_ms))))

    def position_ms(self) -> int:
        """返回当前播放位置（毫秒），取自工作线程维护的缓存，任意线程可安全调用。"""
        with self._lock:
            return int(self._position_ms)

    def duration_ms(self) -> int:
        """返回当前媒体时长（毫秒），取自缓存。"""
        with self._lock:
            return int(self._duration_ms)

    def is_busy(self) -> bool:
        with self._lock:
            return self._state in ("playing", "paused")

    @property
    def state(self) -> str:
        """当前状态：'idle' | 'playing' | 'paused'"""
        return self._state

    # ── 工作线程 ─────────────────────────────────────────────────────────

    def _post(self, kind: str, payload=None, *, wait: bool = True):
        """投递一条 MCI 操作给工作线程。

        已在工作线程上时（多为回调里触发的 stop）直接执行，避免自己等自己。
        """
        if threading.current_thread() is self._worker:
            return self._handle(kind, payload)
        request = _Request(kind, payload)
        self._requests.put(request)
        if not wait:
            return None
        request.done.wait(self._REQUEST_TIMEOUT)
        return request.result

    def _run(self) -> None:
        """工作线程主循环：串行执行命令，并在空档轮询播放状态。"""
        while not self._closed.is_set():
            try:
                request = self._requests.get(timeout=self._TICK_INTERVAL)
            except queue.Empty:
                self._safe_tick()
                continue
            if request.kind == "quit":
                request.done.set()
                return
            try:
                request.result = self._handle(request.kind, request.payload)
            except Exception as exc:
                logger.error("[MciMusicPlayer] 命令 %s 异常: %s", request.kind, exc)
                request.result = None
            finally:
                request.done.set()
            self._safe_tick()

    def _safe_tick(self) -> None:
        """轮询一次播放状态；任何异常都不得让工作线程退出。"""
        try:
            self._tick()
        except Exception as exc:
            logger.debug("[MciMusicPlayer] 轮询异常: %s", exc)

    def _handle(self, kind: str, payload):
        if kind == "play":
            return self._handle_play(*payload)
        if kind == "pause":
            return self._handle_pause()
        if kind == "resume":
            return self._handle_resume()
        if kind == "stop":
            return self._handle_stop()
        if kind == "volume":
            return self._handle_volume(payload)
        if kind == "seek":
            return self._handle_seek(payload)
        return None

    def _handle_play(self, path: str, volume: float, generation: int):
        self._close_alias()                      # 单线程内先关掉上一首，绝不与之重叠
        alias = "cm_" + uuid.uuid4().hex[:8]
        ret = _mci(f'open "{path}" type mpegvideo alias {alias}')
        logger.debug("[MciMusicPlayer] open %s: ret=%s, file=%s", alias, ret, path)
        if ret != 0:
            logger.debug("[MciMusicPlayer] mpegvideo 打开失败，尝试不带 type")
            ret = _mci(f'open "{path}" alias {alias}')
        if ret != 0:
            logger.error("[MciMusicPlayer] 无法打开音频文件: %s (ret=%s)", path, ret)
            return None

        _mci(f'set {alias} time format milliseconds')
        try:
            duration_ms = max(0, int(_mci_query(f'status {alias} length') or 0))
        except Exception:
            duration_ms = 0

        vol = max(0, min(1000, int(volume * 1000)))
        ret = _mci(f'setaudio {alias} volume to {vol}')
        logger.debug("[MciMusicPlayer] setaudio %s volume to %s: ret=%s", alias, vol, ret)
        ret = _mci(f'play {alias}')
        logger.debug("[MciMusicPlayer] play %s: ret=%s", alias, ret)
        if ret != 0:
            logger.error("[MciMusicPlayer] 无法播放音频文件: %s (ret=%s)", path, ret)
            _mci(f'close {alias}')
            return None

        with self._lock:
            self._alias       = alias
            self._state       = "playing"
            self._generation  = int(generation)
            self._duration_ms = duration_ms
            self._position_ms = 0
        return {
            "alias":       alias,
            "generation":  int(generation),
            "duration_ms": duration_ms,
        }

    def _handle_pause(self) -> bool:
        with self._lock:
            if self._state != "playing":
                return False
            alias = self._alias
        if alias:
            _mci(f'pause {alias}')
        with self._lock:
            self._state = "paused"
        return True

    def _handle_resume(self) -> bool:
        with self._lock:
            if self._state != "paused":
                return False
            alias = self._alias
        if alias:
            _mci(f'resume {alias}')
        with self._lock:
            self._state = "playing"
        return True

    def _handle_stop(self) -> bool:
        self._close_alias()
        return True

    def _handle_volume(self, volume: float) -> bool:
        with self._lock:
            alias = self._alias
            state = self._state
        if alias and state in ("playing", "paused"):
            vol = max(0, min(1000, int(volume * 1000)))
            _mci(f'setaudio {alias} volume to {vol}')
            return True
        return False

    def _handle_seek(self, position_ms: int) -> bool:
        with self._lock:
            alias = self._alias
            state = self._state
        if not alias or state not in ("playing", "paused"):
            return False
        target = max(0, int(position_ms))
        _mci(f'seek {alias} to {target}')
        _mci(f'play {alias} from {target}')
        if state == "paused":
            _mci(f'pause {alias}')
        with self._lock:
            self._position_ms = target
        return True

    def _close_alias(self) -> None:
        """停止并关闭当前别名；在工作线程内调用。"""
        with self._lock:
            alias = self._alias
            self._alias       = None
            self._state       = "idle"
            self._duration_ms = 0
            self._position_ms = 0
        if alias:
            ret = _mci(f'stop {alias}')
            logger.debug("[MciMusicPlayer] stop %s: ret=%s", alias, ret)
            ret = _mci(f'close {alias}')
            logger.debug("[MciMusicPlayer] close %s: ret=%s", alias, ret)

    def _tick(self) -> None:
        """在工作线程上轮询播放状态：刷新位置缓存，自然播完时收尾。"""
        with self._lock:
            alias = self._alias
            state = self._state
            generation = self._generation
        if not alias or state == "idle":
            return

        ret, mode = _mci_query_result(f'status {alias} mode')
        if ret != 0:
            # 本线程就是 open 别名的线程，这里不该失败；记录但绝不误判成"播完"。
            logger.debug("[MciMusicPlayer] status %s mode 查询失败: ret=%s", alias, ret)
            return

        if mode in ("playing", "paused"):
            if state == "playing":
                position = _mci_query(f'status {alias} position')
                try:
                    with self._lock:
                        if self._alias == alias:
                            self._position_ms = max(0, int(position or 0))
                except (TypeError, ValueError):
                    pass
            return

        if mode == "stopped":
            self._finish(alias, generation)

    def _finish(self, alias: str, generation: int) -> None:
        """自然播完：关掉别名并回调 ``finished``。"""
        with self._lock:
            if self._alias != alias:
                return
            self._alias       = None
            self._state       = "idle"
            self._duration_ms = 0
            self._position_ms = 0
        ret = _mci(f'close {alias}')
        logger.debug("[MciMusicPlayer] 播放完成，close %s: ret=%s", alias, ret)
        self._emit("finished", generation)
