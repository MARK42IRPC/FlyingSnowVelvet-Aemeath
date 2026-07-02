"""音频核心模块 - 使用 Windows MCI API 实现多音频调度与响度协调

事件格式 (SOUND_REQUEST):
    {
        'audio_type':     str,   # 音频类型（如 'world_sfx' / 'voice'）
        'source':         str,   # 音频源，当前为本地文件绝对路径
        'volume_gain':    float, # 单次事件响度系数 0.0-1.0
        'interruptible':  bool,  # 是否可被后续申请打断
    }

实现说明:
    使用 winmm.mciSendStringW 驱动 Windows Media Control Interface (MCI)，
    原生支持 MP3，无需 QMediaPlayer / PyQt5.QtMultimedia，无额外依赖。
    每个频道的播放在独立 daemon 线程中进行，轮询 MCI status 判断结束。
"""
import os
import ctypes
import threading
import uuid
from typing import Optional, List, Callable

from lib.core.event.center import get_event_center, EventType, Event
from config.audio import get_effective_sound_volume


# ── Windows MCI ────────────────────────────────────────────────────────
_winmm = ctypes.windll.winmm
# 序列化 MCI 调用，保证线程安全（每次调用本身极短，不影响并发播放）
_mci_lock = threading.Lock()


def _mci(cmd: str) -> int:
    """发送 MCI 命令（无返回值版本，线程安全）"""
    with _mci_lock:
        return _winmm.mciSendStringW(cmd, None, 0, None)


def _mci_query(cmd: str, buf_size: int = 64) -> str:
    """发送 MCI 查询命令，返回结果字符串"""
    buf = ctypes.create_unicode_buffer(buf_size)
    with _mci_lock:
        _winmm.mciSendStringW(cmd, buf, buf_size, None)
    return buf.value


# ── 频道参数 ───────────────────────────────────────────────────────────
_MAX_CHANNELS = 4

# 多频道并发时的响度缩放因子（防止叠加失真）
_VOLUME_SCALE = {1: 1.0, 2: 0.75, 3: 0.60, 4: 0.50}
_VOICE_AUDIO_TYPES = {"voice", "priority_voice"}
_PRIORITY_VOICE_AUDIO_TYPES = {"priority_voice"}


# ==============================================================================
class _AudioChannel:
    """单个异步音频频道（Windows MCI + daemon 线程）"""

    # MCI 音量范围 0-1000，对应 base_volume (0-100) * 10
    _MCI_VOL_SCALE = 10
    # 播放结束轮询间隔（秒）
    _POLL_INTERVAL = 0.1

    def __init__(self, on_stop: Callable):
        self._on_stop = on_stop
        self._state_lock = threading.Lock()  # 保护状态变量的线程锁
        self.active = False
        self.audio_type: str = ''   # 当前正在播放的音频类型
        self.interruptible = True
        self.base_volume = 100   # 0-100，由 play() 写入
        self._alias: Optional[str] = None
        self._stop_flag = threading.Event()

    # ------------------------------------------------------------------
    def play(self, source: str, volume: float, interruptible: bool):
        """开始播放指定文件（非阻塞）"""
        # 通知旧线程尽快退出
        self._stop_flag.set()

        alias = 'sx_' + uuid.uuid4().hex[:8]
        with self._state_lock:
            self.base_volume = max(0, min(100, int(volume * 100)))
            self.interruptible = interruptible
            self.active = True
            self._alias = alias
        self._stop_flag = threading.Event()
        stop_flag = self._stop_flag

        threading.Thread(
            target=self._play_thread,
            args=(source, alias, self.base_volume, stop_flag),
            daemon=True,
            name=f'audio-{alias}',
        ).start()

    # ------------------------------------------------------------------
    def _play_thread(self, source: str, alias: str,
                     vol: int, stop_flag: threading.Event):
        """播放线程：打开 → 设音量 → 播放 → 轮询结束 → 关闭"""
        opened = False
        try:
            ret = _mci(f'open "{source}" type mpegvideo alias {alias}')
            if ret != 0:
                # 打开失败时重置状态
                with self._state_lock:
                    if self._alias == alias:
                        self.active = False
                return
            opened = True

            _mci(f'setaudio {alias} volume to {vol * self._MCI_VOL_SCALE}')

            if stop_flag.is_set():
                return

            _mci(f'play {alias}')   # 非阻塞，立即返回

            # 轮询等待播放结束
            while not stop_flag.is_set():
                mode = _mci_query(f'status {alias} mode')
                if mode in ('stopped', ''):
                    break
                stop_flag.wait(self._POLL_INTERVAL)

        finally:
            if opened:
                _mci(f'close {alias}')
            # 正常结束（非被打断）时通知核心重新平衡响度
            # 使用锁保护状态检查和更新，避免与stop()竞态
            should_notify = False
            with self._state_lock:
                if not stop_flag.is_set() and self._alias == alias:
                    self.active = False
                    should_notify = True
            if should_notify:
                self._on_stop()

    # ------------------------------------------------------------------
    def stop(self):
        """中止当前播放（线程会在下次轮询时退出）"""
        self._stop_flag.set()
        with self._state_lock:
            current_alias = self._alias
            self.active = False
        if current_alias:
            _mci(f'stop {current_alias}')

    def set_scaled_volume(self, scale: float):
        """按缩放因子调整实际播放音量（不修改 base_volume）"""
        with self._state_lock:
            current_alias = self._alias
            is_active = self.active
            base_vol = self.base_volume
        if current_alias and is_active:
            vol = int(base_vol * scale * self._MCI_VOL_SCALE)
            _mci(f'setaudio {current_alias} volume to {vol}')


# ==============================================================================
class VoiceCore:
    """
    音频核心（单例）

    职责：
    - 接收 SOUND_REQUEST 事件
    - 管理固定数量的音频频道（_MAX_CHANNELS）
    - 多音频并发时自动缩放各频道响度，避免叠加失真
    - 按 interruptible 标志决定是否可抢占已占用频道
    """

    def __init__(self):
        self._channels: List[_AudioChannel] = [
            _AudioChannel(on_stop=self._rebalance_volumes)
            for _ in range(_MAX_CHANNELS)
        ]
        self._ec = get_event_center()
        self._ec.subscribe(EventType.SOUND_REQUEST, self._on_sound_request)
        # 订阅预启动事件，在 3 秒等待期内完成 MCI 预热，消除首次播放卡顿
        self._ec.subscribe(EventType.APP_PRE_START, self._on_pre_start)

    # ------------------------------------------------------------------
    def _on_pre_start(self, event: Event):
        """
        APP_PRE_START 回调：预热 Windows MCI 多媒体子系统。

        首次调用 mciSendStringW open 时 Windows 需要加载编解码器，耗时较长。
        在启动的 3 秒等待期内提前执行一次 open+close，后续播放无感知延迟。
        """
        self._ec.unsubscribe(EventType.APP_PRE_START, self._on_pre_start)
        working_dir: str = event.data.get('working_dir', '')
        if not working_dir:
            return
        import glob
        pattern = os.path.join(working_dir, 'resc', 'SOUND', '**', '*.mp3')
        files = glob.glob(pattern, recursive=True)
        if not files:
            return
        alias = 'warmup_' + uuid.uuid4().hex[:6]
        # open + close 触发编解码器加载，不实际播放
        _mci(f'open "{files[0]}" type mpegvideo alias {alias}')
        _mci(f'close {alias}')

    # ------------------------------------------------------------------
    def _on_sound_request(self, event: Event):
        """处理音频播放申请"""
        data = event.data
        audio_type: str = str(data.get('audio_type', '') or '').strip()
        source: str = str(data.get('source', '') or '').strip()
        volume_gain: float = float(data.get('volume_gain', 1.0))
        interruptible: bool = bool(data.get('interruptible', True))

        if not audio_type or not source or not os.path.isfile(source):
            return

        if audio_type in _PRIORITY_VOICE_AUDIO_TYPES:
            self._stop_voice_channels()
        # 普通语音互斥：高优先级语音播放时，普通语音直接丢弃。
        elif audio_type in _VOICE_AUDIO_TYPES and self._is_voice_playing():
            event.mark_handled()
            return

        volume = get_effective_sound_volume(audio_type, volume_gain)

        # 优先使用空闲频道
        channel = self._free_channel()

        # 无空闲频道时，尝试打断一个可中断的频道
        if channel is None:
            channel = self._interruptible_channel()
            if channel is None:
                return  # 所有频道均被不可打断的音频占用，丢弃申请
            channel.stop()

        channel.audio_type = audio_type
        channel.play(source, volume, interruptible)
        self._rebalance_volumes()
        event.mark_handled()

    def _is_voice_playing(self) -> bool:
        """检查是否已有语音类音频正在播放。"""
        return any(
            ch.active and ch.audio_type in _VOICE_AUDIO_TYPES
            for ch in self._channels
        )

    def _stop_voice_channels(self) -> None:
        """停止当前所有语音类频道，让高优先级语音立即接管。"""
        for ch in self._channels:
            if ch.active and ch.audio_type in _VOICE_AUDIO_TYPES:
                ch.stop()

    def is_type_playing(self, audio_type: str) -> bool:
        """检查指定音频类型是否正在任一频道播放"""
        return any(ch.active and ch.audio_type == audio_type for ch in self._channels)

    def is_class_playing(self, audio_class: str) -> bool:
        """兼容旧接口，audio_class 已等价于 audio_type。"""
        return self.is_type_playing(audio_class)

    # ------------------------------------------------------------------
    def _free_channel(self) -> Optional[_AudioChannel]:
        """返回第一个空闲频道，无则返回 None"""
        for ch in self._channels:
            if not ch.active:
                return ch
        return None

    def _interruptible_channel(self) -> Optional[_AudioChannel]:
        """返回第一个活跃且可打断的频道，无则返回 None"""
        for ch in self._channels:
            if ch.active and ch.interruptible:
                return ch
        return None

    def _rebalance_volumes(self):
        """按活跃频道数重新平衡各频道响度"""
        active = [ch for ch in self._channels if ch.active]
        count = len(active)
        if count == 0:
            return
        scale = _VOLUME_SCALE.get(count, 0.45)
        for ch in active:
            ch.set_scaled_volume(scale)

    # ------------------------------------------------------------------
    def cleanup(self):
        """释放所有音频资源"""
        self._ec.unsubscribe(EventType.SOUND_REQUEST, self._on_sound_request)
        for ch in self._channels:
            ch.stop()


# ==============================================================================
_voice_core: Optional[VoiceCore] = None


def get_voice_core() -> VoiceCore:
    """获取全局 VoiceCore 单例"""
    global _voice_core
    if _voice_core is None:
        _voice_core = VoiceCore()
    return _voice_core


def cleanup_voice_core():
    """清理全局 VoiceCore 单例"""
    global _voice_core
    if _voice_core is not None:
        _voice_core.cleanup()
        _voice_core = None
