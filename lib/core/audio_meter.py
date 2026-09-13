"""系统音频输出峰值与频段强度检测器

通过 WASAPI IAudioMeterInformation 获取当前系统默认输出设备的实时峰值（0.0–1.0），
并通过 `lib.core.audio_spectrum` 的回环流分析节奏频段（默认 `SPEAKER_AUDIO` 的
`freq_min`–`freq_max`）能量，供音响视觉跟随鼓点形变。

使用方式：
    from lib.core.audio_meter import get_audio_meter

    meter = get_audio_meter()       # 单例
    peak  = meter.get_peak()        # 0.0–1.0，采样无锁，可在任意线程调用
    level = meter.get_frequency_intensity()   # 0.0–1.0，优先取回环频段能量

设计原则：
  - 单例延迟初始化，pycaw 不可用时静默降级（始终返回 0.0）
  - 使用后台采样线程缓存峰值，主线程按需读取，避免高频 COM 调用阻塞 UI
  - 线程安全：采样线程初始化COM，前台读取只读缓存
"""

from __future__ import annotations

import logging
import math
import threading

logger = logging.getLogger(__name__)

_SPECTRUM_DEFAULTS = {
    "freq_min": 60.0,
    "freq_max": 250.0,
    "level_floor_db": -50.0,
    "level_ceil_db": 0.0,
}


def _spectrum_config() -> dict:
    """读取音响视觉的频段与映射参数，缺失或非法时用内置默认值。"""
    try:
        from config.config_music import SPEAKER_AUDIO
    except Exception:
        return dict(_SPECTRUM_DEFAULTS)
    config = {}
    for key, fallback in _SPECTRUM_DEFAULTS.items():
        try:
            config[key] = float(SPEAKER_AUDIO.get(key, fallback))
        except (TypeError, ValueError):
            config[key] = fallback
    return config


class AudioMeter:
    """
    系统默认音频输出设备的实时峰值读取器。

    - 初始化失败时静默降级，`get_peak()` 始终返回 0.0
    - 不持有后台线程，由调用方按帧驱动采样
    - 线程安全：每次调用前初始化COM
    """

    def __init__(self) -> None:
        self._meter = None
        self._peak = 0.0
        self._peak_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._spectrum = None
        self._spectrum_config = _spectrum_config()
        self._init_meter()
        self._start_sampler()
        self._start_spectrum()

    def _init_meter(self) -> None:
        """初始化 pycaw COM 接口；失败时静默降级。"""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
            from comtypes import CLSCTX_ALL

            speakers = AudioUtilities.GetSpeakers()
            dev = speakers._dev
            # 用 QueryInterface 取接口：ctypes.cast 不增加引用计数，会让 Activate
            # 的临时指针与结果指针共享同一个 COM 引用，临时指针先析构时把仍在使用的
            # 接口提前释放（退出期表现为 access violation 与 “COM method call
            # without VTable”）。QueryInterface 返回自带引用的指针，pycaw 自身也这样做。
            activated = dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
            self._meter = activated.QueryInterface(IAudioMeterInformation)
            logger.debug("[AudioMeter] IAudioMeterInformation 初始化成功")
        except ImportError:
            logger.warning("[AudioMeter] pycaw 未安装，响度检测不可用")
        except Exception as e:
            logger.warning(f"[AudioMeter] 初始化失败，响度检测不可用: {e}")

    def _ensure_com_initialized(self) -> None:
        """确保当前线程已初始化COM（多线程安全）。"""
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass  # COM可能已初始化或初始化失败，忽略

    def _start_sampler(self) -> None:
        """启动后台采样线程，避免 UI 高频路径直接触发 COM 调用。"""
        if self._meter is None or self._worker is not None:
            return
        self._worker = threading.Thread(
            target=self._sampler_loop,
            daemon=True,
            name="audio-meter-sampler",
        )
        self._worker.start()

    def _start_spectrum(self) -> None:
        """启动回环频段分析；不可用时保持 None 并回退峰值路径。"""
        try:
            from lib.core.audio_spectrum import AudioSpectrumAnalyzer

            self._spectrum = AudioSpectrumAnalyzer(
                freq_min=self._spectrum_config["freq_min"],
                freq_max=self._spectrum_config["freq_max"],
            )
        except Exception as exc:
            self._spectrum = None
            logger.warning(f"[AudioMeter] 频段分析不可用，回退峰值路径: {exc}")

    def _read_peak_once(self) -> float:
        if self._meter is None:
            return 0.0
        try:
            self._ensure_com_initialized()
            return float(self._meter.GetPeakValue())
        except Exception:
            return 0.0

    def _sampler_loop(self) -> None:
        """后台定时采样系统音频峰值。"""
        interval = 1.0 / 30.0  # 30Hz 已足够驱动响度动画
        self._ensure_com_initialized()
        while not self._stop_event.is_set():
            peak = self._read_peak_once()
            with self._peak_lock:
                self._peak = peak
            self._stop_event.wait(interval)

    def get_peak(self) -> float:
        """
        返回当前系统音频输出峰值（0.0–1.0）。

        返回后台采样缓存值；无可用设备时返回 0.0。
        """
        with self._peak_lock:
            return self._peak

    def get_frequency_intensity(self) -> float | None:
        """
        返回当前节奏频段的强度（0.0–1.0）。

        优先使用 WASAPI 回环 + FFT 的目标频段能量；回环不可用或还没有数据时，
        回退到整体峰值开方，保证没有回环能力的机器仍有响度动画。
        """
        level_db = self.get_band_level_db()
        if level_db is not None:
            # 延迟导入，避免不需要频段分析时加载频谱模块。
            from lib.core.audio_spectrum import level_to_intensity

            return level_to_intensity(
                level_db,
                self._spectrum_config["level_floor_db"],
                self._spectrum_config["level_ceil_db"],
            )
        peak = self.get_peak()
        if peak > 0.01:
            return min(1.0, math.sqrt(peak))
        return 0.0

    def get_band_level_db(self) -> float | None:
        """返回最近一次节奏频段能量（dB）；回环不可用或没有数据时返回 None。"""
        spectrum = self._spectrum
        if spectrum is None:
            return None
        try:
            return spectrum.get_level_db()
        except Exception:
            return None

    def get_frequency_source(self) -> str:
        """返回当前强度来源（诊断用）：`spectrum` / `peak` / `none`。"""
        if self.get_band_level_db() is not None:
            return "spectrum"
        return "peak" if self.get_peak() > 0.01 else "none"

    def spectrum_status(self) -> dict:
        """返回回环频段分析的只读状态（诊断用）。"""
        spectrum = self._spectrum
        if spectrum is None:
            return {"ready": False, "reason": "频段分析未启动", "level_db": None}
        try:
            return spectrum.status()
        except Exception as exc:
            return {"ready": False, "reason": str(exc), "level_db": None}

    def cleanup(self) -> None:
        """停止后台采样线程。"""
        self._stop_event.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=0.2)
        self._worker = None
        # 在 COM 仍可用时显式放掉 meter 指针，退出期不再依赖 GC 与 COM 拆卸的先后。
        self._meter = None
        spectrum = self._spectrum
        self._spectrum = None
        if spectrum is not None:
            try:
                spectrum.cleanup()
            except Exception:
                pass


# ── 单例访问 ──────────────────────────────────────────────────────────────
_instance: AudioMeter | None = None


def get_audio_meter() -> AudioMeter:
    """获取 AudioMeter 全局单例（首次调用时创建）。"""
    global _instance
    if _instance is None:
        _instance = AudioMeter()
    return _instance


def cleanup_audio_meter() -> None:
    """释放 AudioMeter 全局单例。"""
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
