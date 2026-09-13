"""系统混音频段能量分析（WASAPI loopback + rFFT）。

音响视觉原先只能用 `IAudioMeterInformation` 的整体峰值再开平方，任何声音都会被
映射成接近满格的值，形变因此“挤成一坨”、看不出节奏。本模块在默认输出设备上开一条
共享模式回环流，把混音数据做 FFT，只统计 `freq_min`–`freq_max`（默认取鼓点/贝斯的
节奏频段）的能量，再换算成 dB 供调用方映射成 0.0–1.0 的强度。

设计原则与 `lib/core/audio_meter.py` 一致：
  - 全部 COM 初始化与读取都在后台采样线程内完成，主线程只读缓存；
  - 任何一步失败都静默降级（`get_level_db()` 返回 None），由调用方回退峰值路径；
  - 回环流在纯静音时不产生数据包，因此“长时间没有数据”会触发一次重建，兼容
    默认输出设备被切换的情况。
"""

from __future__ import annotations

import ctypes
import logging
import math
import threading
import time

logger = logging.getLogger(__name__)

AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x00000002
WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

_DEFAULT_WINDOW_SIZE = 4096
_SAMPLE_HZ = 60.0
_SILENCE_DB = -120.0
_REBUILD_IDLE_SECONDS = 8.0
_REBUILD_COOLDOWN_SECONDS = 8.0


def _silence_db() -> float:
    return _SILENCE_DB


def band_level_db(
    samples,
    sample_rate: float,
    freq_min: float,
    freq_max: float,
    window_size: int = _DEFAULT_WINDOW_SIZE,
) -> float:
    """返回 `freq_min`–`freq_max` 频段能量的 dB 值（满幅正弦为 0 dB）。

    纯函数，便于用合成信号测试：Hann 窗 + rFFT，按窗函数相干增益归一化幅度，
    再把频段内各 bin 的功率求和开方，得到近似带内 RMS。
    """
    try:
        import numpy as np
    except ImportError:
        return _SILENCE_DB
    frames = np.asarray(samples, dtype=np.float32).reshape(-1)
    size = int(window_size)
    if frames.size == 0 or sample_rate <= 0 or size < 8:
        return _SILENCE_DB
    if frames.size < size:
        frames = np.pad(frames, (size - frames.size, 0))
    else:
        frames = frames[-size:]
    window = np.hanning(size).astype(np.float32)
    # Hann 窗相干增益 0.5，幅度归一化因子为 size/2 * 0.5。
    spectrum = np.abs(np.fft.rfft(frames * window)) / (size / 4.0)
    freqs = np.fft.rfftfreq(size, d=1.0 / float(sample_rate))
    low = max(0.0, float(freq_min))
    high = max(low, float(freq_max))
    band = (freqs >= low) & (freqs <= high)
    if not bool(band.any()):
        return _SILENCE_DB
    energy = float(np.sqrt(np.sum(spectrum[band] ** 2))) / math.sqrt(2.0)
    if not math.isfinite(energy) or energy <= 1e-7:
        return _SILENCE_DB
    return 20.0 * math.log10(energy)


def level_to_intensity(level_db: float, floor_db: float, ceil_db: float) -> float:
    """把 dB 值线性映射到 0.0–1.0；低于下限视为 0，高于上限视为 1。"""
    floor = float(floor_db)
    ceil = float(ceil_db)
    if ceil <= floor:
        return 0.0
    value = (float(level_db) - floor) / (ceil - floor)
    return max(0.0, min(1.0, value))


_CAPTURE_CLIENT_CLASS = None


def _declare_capture_client():
    """按需声明 IAudioCaptureClient（pycaw 未提供该接口）。"""
    global _CAPTURE_CLIENT_CLASS
    if _CAPTURE_CLIENT_CLASS is not None:
        return _CAPTURE_CLIENT_CLASS
    from ctypes import HRESULT, POINTER, c_ubyte
    from ctypes import c_uint32 as UINT32
    from ctypes import c_uint64 as UINT64
    from ctypes.wintypes import DWORD
    from comtypes import COMMETHOD, GUID, IUnknown

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
        _methods_ = (
            COMMETHOD(
                [],
                HRESULT,
                "GetBuffer",
                (["out"], POINTER(POINTER(c_ubyte)), "ppData"),
                (["out"], POINTER(UINT32), "pNumFramesToRead"),
                (["out"], POINTER(DWORD), "pdwFlags"),
                (["out"], POINTER(UINT64), "pu64DevicePosition"),
                (["out"], POINTER(UINT64), "pu64QPCPosition"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "ReleaseBuffer",
                (["in"], UINT32, "NumFramesRead"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "GetNextPacketSize",
                (["out"], POINTER(UINT32), "pNumFramesInNextPacket"),
            ),
        )

    _CAPTURE_CLIENT_CLASS = IAudioCaptureClient
    return IAudioCaptureClient


class AudioSpectrumAnalyzer:
    """默认输出设备上的混音频段能量采样器。"""

    def __init__(
        self,
        *,
        freq_min: float,
        freq_max: float,
        window_size: int = _DEFAULT_WINDOW_SIZE,
    ) -> None:
        self._freq_min = float(freq_min)
        self._freq_max = float(freq_max)
        self._window_size = max(64, int(window_size))
        self._lock = threading.Lock()
        self._level_db: float | None = None
        self._sample_rate = 0
        self._channels = 0
        self._reason = ""
        self._ready = False
        self._stop_event = threading.Event()
        self._worker = threading.Thread(
            target=self._sampler_loop,
            daemon=True,
            name="audio-spectrum-sampler",
        )
        self._worker.start()

    # ── 只读状态 ──────────────────────────────────────────────────────

    def get_level_db(self) -> float | None:
        """返回最近一次频段能量（dB）；尚未就绪或不可用时返回 None。"""
        with self._lock:
            return self._level_db

    def status(self) -> dict:
        with self._lock:
            return {
                "ready": self._ready,
                "reason": self._reason,
                "level_db": self._level_db,
                "sample_rate": self._sample_rate,
                "channels": self._channels,
                "freq_min": self._freq_min,
                "freq_max": self._freq_max,
            }

    def cleanup(self) -> None:
        self._stop_event.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=0.5)
        self._worker = None

    # ── 采样线程 ──────────────────────────────────────────────────────

    @staticmethod
    def _ensure_com_initialized() -> None:
        try:
            import pythoncom

            pythoncom.CoInitialize()
        except Exception:
            pass

    def _set_unavailable(self, reason: str) -> None:
        with self._lock:
            self._ready = False
            self._reason = str(reason)
            self._level_db = None

    def _sampler_loop(self) -> None:
        self._ensure_com_initialized()
        interval = 1.0 / _SAMPLE_HZ
        capture = None
        client = None
        window = None
        last_packet_at = time.monotonic()
        last_rebuild_at = 0.0
        while not self._stop_event.is_set():
            if capture is None:
                capture, client, window, error = self._open_capture()
                if capture is None:
                    self._set_unavailable(error)
                    self._stop_event.wait(interval)
                    continue
                with self._lock:
                    self._ready = True
                    self._reason = ""
                last_packet_at = time.monotonic()
                last_rebuild_at = time.monotonic()
            try:
                frames = self._drain_packets(capture, client, window)
            except Exception as exc:
                self._close_capture(client)
                capture, client, window = None, None, None
                self._set_unavailable(f"回环采样失败：{exc}")
                self._stop_event.wait(interval)
                continue
            now = time.monotonic()
            if frames is not None:
                last_packet_at = now
            elif (
                now - last_packet_at >= _REBUILD_IDLE_SECONDS
                and now - last_rebuild_at >= _REBUILD_COOLDOWN_SECONDS
            ):
                # 纯静音时回环流没有数据包；定期重建以跟随默认输出设备切换。
                last_rebuild_at = now
                last_packet_at = now
                self._close_capture(client)
                capture, client, window = None, None, None
                continue
            self._stop_event.wait(interval)
        self._close_capture(client)

    def _open_capture(self):
        """创建回环流；返回 (capture, client, window, error)。"""
        window = None
        try:
            import numpy as np
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities
            from pycaw.api.audioclient import IAudioClient

            device = AudioUtilities.GetSpeakers()._dev
            # 必须用 QueryInterface 取接口，不要用 ctypes.cast 改写 Activate 的结果：
            # cast 不增加引用计数，源指针与结果指针会共享同一个 COM 引用，源指针
            # 先析构时仍在使用的接口被提前释放（退出期表现为 access violation 与
            # “COM method call without VTable”）。QueryInterface 返回自带引用的指针。
            activated = device.Activate(IAudioClient._iid_, CLSCTX_ALL, None)
            client = activated.QueryInterface(IAudioClient)
            mix_format = client.GetMixFormat()
            tag = int(mix_format.contents.wFormatTag)
            channels = max(1, int(mix_format.contents.nChannels))
            sample_rate = int(mix_format.contents.nSamplesPerSec)
            bits = int(mix_format.contents.wBitsPerSample)
            block_align = int(mix_format.contents.nBlockAlign) or channels * (bits // 8)
            client.Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_LOOPBACK,
                10_000_000,
                0,
                mix_format,
                None,
            )
            capture_cls = _declare_capture_client()
            service = client.GetService(capture_cls._iid_)
            capture = service.QueryInterface(capture_cls)
            with self._lock:
                self._sample_rate = sample_rate
                self._channels = channels
            self._tag = tag
            self._bits = bits
            self._block_align = max(1, block_align)
            client.Start()
            window = np.zeros(self._window_size, dtype=np.float32)
            return capture, client, window, ""
        except Exception as exc:
            return None, None, None, f"回环采样不可用：{exc}"

    def _close_capture(self, client) -> None:
        if client is None:
            return
        try:
            client.Stop()
        except Exception:
            pass

    def _drain_packets(self, capture, client, window):
        """读取所有可用数据包并更新滑动窗口，返回新增的单声道样本数。"""
        import numpy as np

        appended = 0
        while True:
            if int(capture.GetNextPacketSize()) == 0:
                break
            data_ptr, frames, flags, _dev_pos, _qpc = capture.GetBuffer()
            frames = int(frames)
            try:
                if frames <= 0:
                    continue
                if int(flags) & AUDCLNT_BUFFERFLAGS_SILENT:
                    chunk = np.zeros(frames, dtype=np.float32)
                else:
                    raw = ctypes.string_at(data_ptr, frames * self._block_align)
                    chunk = self._decode_pcm(np, raw, frames)
                size = self._window_size
                if chunk.size >= size:
                    window[:] = chunk[-size:]
                else:
                    window[:-chunk.size] = window[chunk.size:]
                    window[-chunk.size:] = chunk
                appended += frames
            finally:
                capture.ReleaseBuffer(frames)
        if appended:
            level = band_level_db(
                window,
                self._sample_rate,
                self._freq_min,
                self._freq_max,
                self._window_size,
            )
            with self._lock:
                self._level_db = level
            return appended
        return None

    def _decode_pcm(self, np, raw: bytes, frames: int):
        channels = max(1, self._channels)
        if self._bits == 16:
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        else:
            samples = np.frombuffer(raw, dtype="<f4").astype(np.float32)
        usable = (samples.size // channels) * channels
        if usable <= 0:
            return np.zeros(frames, dtype=np.float32)
        return samples[:usable].reshape(-1, channels).mean(axis=1)


__all__ = [
    "AudioSpectrumAnalyzer",
    "band_level_db",
    "level_to_intensity",
]
