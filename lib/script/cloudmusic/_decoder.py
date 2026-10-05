"""网易云音乐 - 本地音频解码辅助

Windows MCI 原生只认得 MP3/WAV/WMA 等少数容器。扫描本地音乐目录时会收录
FLAC / M4A(AAC) / OGG / Opus 等格式，但这类文件 `open ... type mpegvideo`
会直接失败，用户看到的就是"本地音乐有时候打不开"。

本模块把这些格式解码成 16bit PCM 的 WAV 再交给 MCI。解码优先走 `soundfile`
（libsndfile，随发行版打包）；M4A/AAC 这类 libsndfile 不认的容器回退到 `av`
（PyAV，自带 FFmpeg，仅当运行环境已安装时使用）。两条路径都失败时返回 None，
由调用方给出明确的失败提示，而不是静默无声。

解码结果按 `源文件路径 + 大小 + mtime` 缓存到用户缓存目录的 `decoded/` 下，
换源或改文件会重新解码，重复播放直接命中缓存。所有函数都不导入 Qt。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

#: 解码目标参数：单声道 22.05kHz，足够音乐播放且体积可控。
_DECODE_SAMPLE_RATE = 22050
_DECODE_CHANNELS = 1
_CACHE_LOCK = threading.Lock()
_WARNED_ENGINES: set[str] = set()


def decoded_cache_root() -> Path:
    """返回解码缓存根目录（用户缓存目录下的 ``music/decoded``）。"""
    from config.user_storage_paths import get_user_cache_dir

    return get_user_cache_dir("music") / "decoded"


def _cache_stamp(file_path: Path) -> str:
    stat = file_path.stat()
    payload = f"{file_path}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", "surrogatepass")
    return hashlib.sha256(payload).hexdigest()[:20]


def _load_soundfile():
    try:
        import soundfile
    except Exception:
        return None
    return soundfile


def _load_av():
    try:
        import av
    except Exception:
        return None
    return av


def _warn_once(engine: str, message: str, exc: object) -> None:
    with _CACHE_LOCK:
        if engine in _WARNED_ENGINES:
            return
        _WARNED_ENGINES.add(engine)
    logger.warning("[LocalDecode] %s 不可用，相关格式回退到下一引擎: %s", message, exc)


def _decode_soundfile(file_path: Path, target: Path) -> bool:
    soundfile = _load_soundfile()
    if soundfile is None:
        _warn_once("soundfile-import", "soundfile", "未安装")
        return False
    try:
        data, sample_rate = soundfile.read(
            str(file_path),
            frames=-1,
            dtype="int16",
            always_2d=True,
        )
    except Exception as exc:
        logger.debug("[LocalDecode] soundfile 解码失败 %s: %s", file_path, exc)
        return False

    channels = int(data.shape[1]) if data.ndim > 1 else 1
    if channels <= 0:
        return False
    if channels > 1:
        # 多声道按整数均值混成单声道，先累加再除，避免逐样本截断。
        accumulator = data[:, 0].astype("int64")
        for channel in range(1, channels):
            accumulator += data[:, channel]
        mono = (accumulator // channels).astype("<i2")
    else:
        mono = data[:, 0].astype("<i2")

    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(mono.tobytes())
    return True


def _decode_av(file_path: Path, target: Path) -> bool:
    av = _load_av()
    if av is None:
        _warn_once("av-import", "PyAV", "未安装")
        return False
    try:
        import numpy as np
    except Exception as exc:
        _warn_once("numpy-import", "numpy", exc)
        return False

    try:
        with av.open(str(file_path)) as container:
            streams = [stream for stream in container.streams if stream.type == "audio"]
            if not streams:
                return False
            stream = streams[0]
            resampler = av.AudioResampler(
                format="s16",
                layout="mono",
                rate=_DECODE_SAMPLE_RATE,
            )
            chunks = []
            for frame in container.decode(stream):
                for resampled in resampler.resample(frame):
                    chunks.append(resampled.to_ndarray())
            # AudioResampler 在流尾可能还有残留帧。
            for resampled in resampler.resample(None):
                chunks.append(resampled.to_ndarray())
    except Exception as exc:
        logger.debug("[LocalDecode] PyAV 解码失败 %s: %s", file_path, exc)
        return False

    if not chunks:
        return False
    data = np.concatenate(chunks, axis=1).astype("<i2")
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(_DECODE_CHANNELS)
        handle.setsampwidth(2)
        handle.setframerate(_DECODE_SAMPLE_RATE)
        handle.writeframes(data.tobytes())
    return True


def ensure_decoded_wav(file_path: str | Path) -> Path | None:
    """确保 ``file_path`` 有一份可被 MCI 播放的 WAV 缓存，返回其路径。

    已有缓存直接复用；否则依次尝试 soundfile / PyAV 解码。全部失败返回 None。
    """
    source = Path(file_path)
    if not source.is_file():
        return None

    try:
        stamp = _cache_stamp(source)
    except OSError as exc:
        logger.warning("[LocalDecode] 读取文件信息失败 %s: %s", source, exc)
        return None

    target_dir = decoded_cache_root() / stamp
    target = target_dir / "decoded.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("[LocalDecode] 创建解码缓存目录失败 %s: %s", target_dir, exc)
        return None

    partial = target_dir / "decoded.wav.partial"
    _remove_quietly(partial)
    for decode in (_decode_soundfile, _decode_av):
        try:
            if decode(source, partial):
                partial.replace(target)
                logger.info("[LocalDecode] %s 已解码为 WAV 缓存: %s", source.name, target)
                return target
        except Exception as exc:
            logger.debug("[LocalDecode] %s 解码异常: %s", decode.__name__, exc)
        _remove_quietly(partial)

    logger.warning("[LocalDecode] 无法解码本地音乐，已跳过: %s", source)
    return None


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


__all__ = ["ensure_decoded_wav", "decoded_cache_root"]
