"""网易云音乐 - 本地音频解码辅助

Windows MCI 原生只认得 MP3/WAV/WMA 等少数容器。扫描本地音乐目录时会收录
FLAC / M4A(AAC) / OGG / Opus 等格式，但这类文件 `open ... type mpegvideo`
会直接失败，用户看到的就是"本地音乐有时候打不开"。

本模块把这些格式解码成 16bit PCM 的 WAV 再交给 MCI。解码优先走 `soundfile`
（libsndfile，随发行版打包）；M4A/AAC 这类 libsndfile 不认的容器回退到 `av`
（PyAV，自带 FFmpeg，仅当运行环境已安装时使用）。两条路径都失败时返回 None，
由调用方给出明确的失败提示，而不是静默无声。

**保真约束**：解码目标是「MCI 认得的容器」，不是「省空间的中间格式」，因此采样率
与声道数一律照抄源文件。此前两条解码路径各丢一半：`soundfile` 把结果统一混成了
单声道，`av` 更把目标写死成单声道 22.05kHz（48kHz 音源直接对半砍）。那是可被
听出来的实质损伤，不是体积优化——本地 FLAC / M4A 比在线音源明显更差就来自这里。
只有 MCI 明确不接受的极端参数（超范围采样率、零声道）才会退到安全档。

注意缓存按 `源文件 + 大小 + mtime` 分目录，因此改口径后旧缓存不会被复用；但两者
指纹相同时「旧解码结果」仍可能被认成有效缓存。所以指纹里带一个解码口径版本号：
口径一改就递增，旧目录自然失效，用户不必手动清缓存。

解码结果按 `源文件路径 + 大小 + mtime` 缓存到用户缓存目录的 `decoded/` 下，
换源或改文件会重新解码，重复播放直接命中缓存。所有函数都不导入 Qt。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import wave
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

#: MCI 能接受的采样率区间；超出则退到最近的边界档（极端参数源很少见）。
_MIN_SAMPLE_RATE = 8000
_MAX_SAMPLE_RATE = 192000
#: MCI 实际能混出的通道上限：多于两声道时混成双声道，而不是压成单声道。
_MAX_OUTPUT_CHANNELS = 2
#: 解码口径版本号。改动输出格式（采样率 / 声道策略）时必须 +1：指纹随之变化，
#: 旧的 cache 目录不再命中，用户不会继续听到按旧口径导出的降级音频。
_DECODE_REVISION = 2
_CACHE_LOCK = threading.Lock()
_WARNED_ENGINES: set[str] = set()


class _DecodedAudio(NamedTuple):
    """一次解码的结果：交错排列的 16bit PCM 与它自己的格式描述。"""

    samples: bytes
    channels: int
    sample_rate: int
    frames: int


def _clamp_sample_rate(rate: int) -> int:
    """把采样率夹到 MCI 可接受区间；拿不到有效值时退回 CD 档而不是砍半。"""

    value = int(rate or 0)
    if value <= 0:
        return 44100
    return max(_MIN_SAMPLE_RATE, min(_MAX_SAMPLE_RATE, value))


def decoded_cache_root() -> Path:
    """返回解码缓存根目录（用户缓存目录下的 ``music/decoded``）。"""
    from config.user_storage_paths import get_user_cache_dir

    return get_user_cache_dir("music") / "decoded"


def _cache_stamp(file_path: Path) -> str:
    stat = file_path.stat()
    payload = (
        f"{_DECODE_REVISION}|{file_path}|{stat.st_size}|{stat.st_mtime_ns}"
    ).encode("utf-8", "surrogatepass")
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


def _decode_soundfile(file_path: Path) -> _DecodedAudio | None:
    """用 libsndfile 解码，保持源采样率与声道数（>2 声道混成双声道）。"""

    soundfile = _load_soundfile()
    if soundfile is None:
        _warn_once("soundfile-import", "soundfile", "未安装")
        return None
    try:
        data, sample_rate = soundfile.read(
            str(file_path),
            frames=-1,
            dtype="int16",
            always_2d=True,
        )
    except Exception as exc:
        logger.debug("[LocalDecode] soundfile 解码失败 %s: %s", file_path, exc)
        return None

    channels = int(data.shape[1]) if data.ndim > 1 else 1
    if channels <= 0:
        return None
    if channels == 1:
        # MCI 对单声道 WAV 支持良好，原样保留。
        interleaved = data.astype("<i2")
    elif channels == 2:
        interleaved = data.astype("<i2")
    else:
        # 环绕声按整数均值混成双声道（左组 / 右组交替），先累加再除，避免逐样本截断。
        accumulator = data.astype("int64")
        pairs = accumulator[:, : channels - (channels % 2)].reshape(-1, channels // 2, 2)
        mixed = (pairs.sum(axis=1) // (channels // 2)).astype("<i2")
        interleaved = mixed
        channels = 2
    return _DecodedAudio(
        interleaved.tobytes(),
        channels,
        _clamp_sample_rate(int(sample_rate)),
        int(interleaved.shape[0]),
    )


def _decode_av(file_path: Path) -> _DecodedAudio | None:
    """用 PyAV（FFmpeg）解码，保持源采样率与声道数。"""

    av = _load_av()
    if av is None:
        _warn_once("av-import", "PyAV", "未安装")
        return None
    try:
        import numpy as np
    except Exception as exc:
        _warn_once("numpy-import", "numpy", exc)
        return None

    try:
        with av.open(str(file_path)) as container:
            streams = [stream for stream in container.streams if stream.type == "audio"]
            if not streams:
                return None
            stream = streams[0]
            layout = getattr(stream, "layout", None)
            if layout is None:
                return None
            resampler = av.AudioResampler(
                format="s16p",
                layout=layout,
                rate=int(getattr(stream, "rate", 0) or 0) or None,
            )
            chunks = []
            for frame in container.decode(stream):
                chunks.extend(resampler.resample(frame))
            # AudioResampler 在流尾可能还有残留帧。
            chunks.extend(resampler.resample(None))
    except Exception as exc:
        logger.debug("[LocalDecode] PyAV 解码失败 %s: %s", file_path, exc)
        return None

    if not chunks:
        return None
    # 平面格式得到 (声道, 采样数)；转置后按帧交错，才是 WAV 要的排列。
    planar = np.concatenate([chunk.to_ndarray() for chunk in chunks], axis=1).astype("<i2")
    channels = int(planar.shape[0])
    if channels <= 0:
        return None
    if channels > _MAX_OUTPUT_CHANNELS:
        # 环绕声压成双声道（前半 / 后半各取一组），不压成单声道。
        half = channels // 2
        accumulator = planar.astype("int64")
        planar = (
            (accumulator[:half] + accumulator[half:half * 2]) // 2
        ).astype("<i2")
        channels = _MAX_OUTPUT_CHANNELS
    interleaved = np.ascontiguousarray(planar.T)
    sample_rate = _clamp_sample_rate(int(chunks[0].sample_rate or 0))
    return _DecodedAudio(interleaved.tobytes(), channels, sample_rate, int(interleaved.shape[0]))


def _write_wav(decoded: _DecodedAudio, target: Path) -> bool:
    """把解码结果写成 16bit PCM WAV。"""

    try:
        with wave.open(str(target), "wb") as handle:
            handle.setnchannels(int(decoded.channels))
            handle.setsampwidth(2)
            handle.setframerate(int(decoded.sample_rate))
            handle.writeframes(decoded.samples)
    except (OSError, wave.Error) as exc:
        logger.debug("[LocalDecode] 写入 WAV 失败 %s: %s", target, exc)
        return False
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
            decoded = decode(source)
        except Exception as exc:
            decoded = None
            logger.debug("[LocalDecode] %s 解码异常: %s", decode.__name__, exc)
        if isinstance(decoded, _DecodedAudio) and _write_wav(decoded, partial):
            partial.replace(target)
            logger.info(
                "[LocalDecode] %s 已解码为 WAV 缓存: %s (%dHz/%dch)",
                source.name, target, decoded.sample_rate, decoded.channels,
            )
            return target
        _remove_quietly(partial)

    logger.warning("[LocalDecode] 无法解码本地音乐，已跳过: %s", source)
    return None


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


__all__ = ["ensure_decoded_wav", "decoded_cache_root"]
