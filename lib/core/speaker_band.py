"""每个音响的动感响应频段（response band）。

音响视觉跟随音乐鼓点形变时，响应频段过去固定取全局配置
（`SPEAKER_AUDIO.freq_min` / `freq_max`）。多个音响需要各自跟不同的乐器时，
这里按世界对象实例登记各自的频段：`AudioMeter` 一次回环分析所有登记频段，
音响每帧按自己的 key 取强度，互不影响。

频率与滑条比例之间用对数映射（与听感一致）：比例 1.0 对应 `band_max_hz`，
0.0 对应 `band_min_hz`。滑条不做颗粒吸附，比例是连续值。
"""

from __future__ import annotations

import math
import threading

from config.config_music import SPEAKER_AUDIO

#: 滑条范围与默认频段的兜底值（配置缺失或非法时使用）。
_DEFAULT_BAND_MIN_HZ = 20.0
_DEFAULT_BAND_MAX_HZ = 2000.0
_DEFAULT_RESPONSE_MIN_HZ = 60.0
_DEFAULT_RESPONSE_MAX_HZ = 250.0

#: 两个把手之间至少保留的对数跨度比例，避免上下限重叠成一个点。
MIN_HANDLE_GAP_RATIO = 0.04


def _config_float(key: str, fallback: float) -> float:
    try:
        return float(SPEAKER_AUDIO.get(key, fallback))
    except (TypeError, ValueError, AttributeError):
        return float(fallback)


def band_min_hz() -> float:
    """滑条底端对应的频率（Hz）。"""
    return max(1.0, _config_float('band_slider_min_hz', _DEFAULT_BAND_MIN_HZ))


def band_max_hz() -> float:
    """滑条顶端对应的频率（Hz）。"""
    low = band_min_hz()
    return max(low * 1.01, _config_float('band_slider_max_hz', _DEFAULT_BAND_MAX_HZ))


def default_band() -> tuple[float, float]:
    """配置里的默认响应频段（未单独调整过的音响用它）。"""
    low = _config_float('freq_min', _DEFAULT_RESPONSE_MIN_HZ)
    high = _config_float('freq_max', _DEFAULT_RESPONSE_MAX_HZ)
    return clamp_band(low, high)


def clamp_band(low_hz: float, high_hz: float) -> tuple[float, float]:
    """把上下限夹进滑条范围，并保证下限严格小于上限。"""
    low_limit = band_min_hz()
    high_limit = band_max_hz()
    try:
        low = float(low_hz)
        high = float(high_hz)
    except (TypeError, ValueError):
        low, high = default_band_raw()
    if not math.isfinite(low):
        low = low_limit
    if not math.isfinite(high):
        high = high_limit
    if high < low:
        low, high = high, low
    low = max(low_limit, min(low, high_limit))
    high = max(low_limit, min(high, high_limit))
    gap_ratio = min(0.5, max(0.0, float(MIN_HANDLE_GAP_RATIO)))
    span = high_limit / low_limit
    step = span ** gap_ratio if span > 1.0 else 1.01
    if high < low * step:
        high = low * step
        if high > high_limit:
            high = high_limit
            low = min(low, high / step)
    return (low, high)


def default_band_raw() -> tuple[float, float]:
    """默认频段的原始配置值（不做钳制），供非法输入兜底。"""
    return (
        _config_float('freq_min', _DEFAULT_RESPONSE_MIN_HZ),
        _config_float('freq_max', _DEFAULT_RESPONSE_MAX_HZ),
    )


def frequency_from_ratio(ratio: float) -> float:
    """滑条比例（0.0-1.0）转频率；对数刻度，1.0 为最高频。"""
    low = band_min_hz()
    high = band_max_hz()
    try:
        value = max(0.0, min(1.0, float(ratio)))
    except (TypeError, ValueError):
        value = 0.0
    return low * (high / low) ** value


def ratio_from_frequency(hz: float) -> float:
    """频率转滑条比例（0.0-1.0）；超出范围时夹到两端。"""
    low = band_min_hz()
    high = band_max_hz()
    try:
        value = float(hz)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    value = max(low, min(value, high))
    return math.log(value / low) / math.log(high / low)


def band_ratios(band: tuple[float, float]) -> tuple[float, float]:
    """频段上下限对应的两个滑条比例。"""
    low, high = clamp_band(*band)
    return (ratio_from_frequency(low), ratio_from_frequency(high))


def band_from_ratios(low_ratio: float, high_ratio: float) -> tuple[float, float]:
    """两个滑条比例转频段上下限。"""
    low = frequency_from_ratio(low_ratio)
    high = frequency_from_ratio(high_ratio)
    return clamp_band(low, high)


def band_label(band: tuple[float, float]) -> str:
    """频段的中文短标签，用于气泡提示。"""
    low, high = clamp_band(*band)
    return f'{int(round(low))}–{int(round(high))} Hz'


def band_key(backend_id: str, instance_id: int) -> str:
    """世界对象实例的频段登记 key；身份非法时返回空串。"""
    backend = str(backend_id or "").strip()
    try:
        instance = int(instance_id)
    except (TypeError, ValueError):
        return ""
    if not backend or instance <= 0:
        return ""
    return f"{backend}:{instance}"


_bands: dict[str, tuple[float, float]] = {}
_bands_lock = threading.Lock()


def get_speaker_band(backend_id: str, instance_id: int) -> tuple[float, float]:
    """返回某个音响当前的响应频段；没单独调整过时返回默认频段。"""
    key = band_key(backend_id, instance_id)
    if not key:
        return default_band()
    with _bands_lock:
        band = _bands.get(key)
    return default_band() if band is None else band


def set_speaker_band(
    backend_id: str,
    instance_id: int,
    low_hz: float,
    high_hz: float,
) -> tuple[float, float]:
    """记录某个音响的响应频段，返回钳制后的实际值。"""
    band = clamp_band(low_hz, high_hz)
    key = band_key(backend_id, instance_id)
    if key:
        with _bands_lock:
            _bands[key] = band
    return band


def forget_speaker_band(backend_id: str, instance_id: int) -> None:
    """音响消失时清掉登记，避免频段表无限增长。"""
    key = band_key(backend_id, instance_id)
    if not key:
        return
    with _bands_lock:
        removed = _bands.pop(key, None) is not None
    if removed:
        _unregister_from_meter(key)


def clear_speaker_bands() -> None:
    """清空所有登记（测试与退出清理用）。"""
    with _bands_lock:
        keys = tuple(_bands)
        _bands.clear()
    for key in keys:
        _unregister_from_meter(key)


def registered_bands() -> tuple[tuple[str, tuple[float, float]], ...]:
    """当前登记表快照（诊断与测试用）。"""
    with _bands_lock:
        return tuple(_bands.items())


def speaker_response_intensity(
    backend_id: str,
    instance_id: int,
) -> float | None:
    """按音响自己的响应频段取一次强度（0.0-1.0）。

    这是音响每帧调用的入口：登记是幂等的，频段没变时 `AudioMeter` 直接返回，
    所以这里既保证「换过频段的音响立刻生效」，也不需要额外的同步事件。
    """
    key = band_key(backend_id, instance_id)
    meter = _meter()
    if meter is None:
        return None
    if not key:
        return meter.get_frequency_intensity()
    with _bands_lock:
        band = _bands.get(key)
    if band is None or band == default_band():
        return meter.get_frequency_intensity()
    meter.register_band(key, band[0], band[1])
    return meter.get_frequency_intensity(key)


def _unregister_from_meter(key: str) -> None:
    meter = _meter()
    if meter is None:
        return
    try:
        meter.unregister_band(key)
    except Exception:
        pass


def _meter():
    try:
        from lib.core.audio_meter import get_audio_meter

        return get_audio_meter()
    except Exception:
        return None


__all__ = [
    "MIN_HANDLE_GAP_RATIO",
    "band_from_ratios",
    "band_key",
    "band_label",
    "band_max_hz",
    "band_min_hz",
    "band_ratios",
    "clamp_band",
    "clear_speaker_bands",
    "default_band",
    "forget_speaker_band",
    "frequency_from_ratio",
    "get_speaker_band",
    "ratio_from_frequency",
    "registered_bands",
    "set_speaker_band",
    "speaker_response_intensity",
]
