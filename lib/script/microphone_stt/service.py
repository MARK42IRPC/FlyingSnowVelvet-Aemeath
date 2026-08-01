"""麦克风语音转文本服务。"""

import array
import json
import math
import os
import queue
import re
import shutil
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

from config.config import VOICE
from config.shared_storage import get_shared_root_dir
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_SAMPLE_RATE = 16000
_DEFAULT_BLOCK_SIZE = 8000
_DEFAULT_CHANNELS = 1
_DEFAULT_DTYPE = "int16"
_DEFAULT_QUEUE_SIZE = 32
_DEFAULT_SILENCE_TIMEOUT_SECS = 3.0
_DEFAULT_SPEECH_RMS_THRESHOLD = 550
_DEFAULT_DENOISE_STRENGTH = 0.65
_DEFAULT_NOISE_GATE_THRESHOLD = 180
_MODEL_ENV_KEYS = ("VOSK_MODEL_PATH", "VOSK_MODEL_DIR")
_MODEL_DIR_CANDIDATES = (
    "resc/models/vosk-model-small-cn-0.22",
    "resc/models/vosk-model-small-en-us-0.15",
    "resc/models/vosk",
    "resc/model/vosk",
    "resc/vosk-model",
    "model/vosk",
)
_MODEL_REQUIRED_MARKERS = ("am", "conf")
_BUNDLED_MODEL_RELATIVE_PATH = Path("resc") / "models" / "vosk-model-small-cn-0.22"
_MODEL_DYNAMIC_SEARCH_ROOTS: tuple[Path, ...] = (
    Path("resc") / "models",
    Path("resc") / "model",
    Path("resc"),
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def denoise_pcm16(
    chunk: bytes,
    *,
    strength: float = _DEFAULT_DENOISE_STRENGTH,
    gate_threshold: int = _DEFAULT_NOISE_GATE_THRESHOLD,
    noise_floor: float | None = None,
) -> tuple[bytes, float]:
    """对单声道 PCM16 应用轻量自适应噪声门。

    低于噪声门的样本按强度衰减，高于噪声门的语音样本保持原幅度；
    返回处理后的 PCM 和用于下一块音频的噪声底估计。
    """
    if not chunk:
        return chunk, max(0.0, float(noise_floor or 0.0))
    try:
        samples = array.array('h')
        samples.frombytes(chunk)
        if not samples:
            return chunk, max(0.0, float(noise_floor or 0.0))
    except Exception:
        return chunk, max(0.0, float(noise_floor or 0.0))

    strength_value = max(0.0, min(1.0, float(strength)))
    gate = max(0, min(32767, int(gate_threshold)))
    rms = math.sqrt(sum(int(sample) * int(sample) for sample in samples) / len(samples))
    floor = max(0.0, float(noise_floor or gate))
    if rms <= max(float(gate) * 2.0, floor * 2.0):
        floor = floor * 0.96 + rms * 0.04
    effective_gate = max(float(gate), floor * 1.35)
    if effective_gate <= 0.0 or strength_value <= 0.0:
        return chunk, floor

    for index, sample in enumerate(samples):
        magnitude = abs(int(sample))
        if magnitude >= effective_gate:
            continue
        ratio = max(0.0, min(1.0, magnitude / effective_gate))
        # The exponent makes quiet background sound fall away quickly while
        # leaving samples close to the speech boundary mostly intact.
        gain = ratio ** (1.0 + 4.0 * strength_value)
        samples[index] = max(-32768, min(32767, int(round(sample * gain))))
    return samples.tobytes(), floor


def _is_ascii_path_text(value: str) -> bool:
    return all(ord(ch) < 128 for ch in str(value or ""))


def _is_valid_model_dir(path: Path | None) -> bool:
    if path is None or not path.is_dir():
        return False
    return all((path / marker).exists() for marker in _MODEL_REQUIRED_MARKERS)


def _shared_model_root() -> Path:
    return get_shared_root_dir() / "models" / "vosk"


def _project_relative_model_arg(path: Path) -> str | None:
    try:
        relative = path.resolve().relative_to(_project_root().resolve())
    except Exception:
        return None
    relative_text = str(relative)
    if not relative_text:
        return None
    return relative_text if _is_ascii_path_text(relative_text) else None


def _ensure_ascii_model_mirror(source: Path) -> Path | None:
    if not _is_valid_model_dir(source):
        return None

    target = _shared_model_root()
    if _is_valid_model_dir(target):
        return target

    tmp_target = target.parent / f"{target.name}_tmp"
    try:
        if tmp_target.exists():
            shutil.rmtree(tmp_target, ignore_errors=True)
        tmp_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, tmp_target, dirs_exist_ok=True)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        tmp_target.replace(target)
        return target
    except Exception as e:
        logger.warning("[MicrophoneSttService] 复制 ASCII 兼容模型目录失败: %s", e)
        try:
            if tmp_target.exists():
                shutil.rmtree(tmp_target, ignore_errors=True)
        except Exception:
            pass
        return None


def _resolve_model_load_arg(model_path: Path) -> tuple[Path, str]:
    relative_arg = _project_relative_model_arg(model_path)
    if relative_arg:
        return model_path, relative_arg

    absolute_text = str(model_path)
    if _is_ascii_path_text(absolute_text):
        return model_path, absolute_text

    mirrored = _ensure_ascii_model_mirror(model_path)
    if mirrored is not None:
        return mirrored, str(mirrored)

    return model_path, absolute_text


def _discover_bundled_model_dirs(root_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for relative in _MODEL_DYNAMIC_SEARCH_ROOTS:
        base = root_dir / relative
        if not base.is_dir():
            continue
        try:
            entries = list(base.iterdir())
        except Exception:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            name = entry.name.lower()
            if "vosk" not in name:
                continue
            if _is_valid_model_dir(entry):
                candidates.append(entry)
    return candidates


def _normalize_text(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _coerce_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _coerce_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _extract_text(payload: str, key: str) -> str:
    try:
        data = json.loads(payload or "{}")
    except Exception:
        return ""
    return _normalize_text(data.get(key, ""))


def _merge_text_segments(*parts: str) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = _normalize_text(part)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return _normalize_text(" ".join(merged))


_ENGLISH_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9']*")


def _strip_english_words(text: str) -> str:
    cleaned = re.sub(r"[A-Za-z0-9'`]+", "", str(text or ""))
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def _extract_english_words(text: str) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for word in _ENGLISH_WORD_RE.findall(str(text or "")):
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        words.append(word)
    return words


def _merge_language_candidates(candidates: list[str]) -> str:
    english_words: list[str] = []
    non_english_segments: list[str] = []

    for candidate in candidates:
        normalized = _normalize_text(candidate)
        if not normalized:
            continue
        stripped = _strip_english_words(normalized)
        if stripped:
            non_english_segments.append(stripped)
        for word in _extract_english_words(normalized):
            english_words.append(word)

    non_english_text = "".join(non_english_segments)
    seen_words: set[str] = set()
    deduped_words: list[str] = []
    for word in english_words:
        key = word.lower()
        if key in seen_words:
            continue
        seen_words.add(key)
        deduped_words.append(word)
    english_text = " ".join(deduped_words).strip()

    if non_english_text and english_text:
        return f"{non_english_text} {english_text}"
    return non_english_text or english_text


def _parse_vosk_payload(payload: str, text_key: str) -> tuple[str, float]:
    try:
        data = json.loads(payload or "{}")
    except Exception:
        return "", 0.0

    text = _normalize_text(data.get(text_key, ""))
    confidence_values: list[float] = []

    def _append_conf(value):
        try:
            confidence_values.append(float(value))
        except (TypeError, ValueError):
            pass

    result_entries = data.get("result")
    if isinstance(result_entries, list):
        for entry in result_entries:
            if isinstance(entry, dict):
                _append_conf(entry.get("conf"))
                _append_conf(entry.get("confidence"))

    alternatives = data.get("alternatives")
    if isinstance(alternatives, list):
        for entry in alternatives:
            if isinstance(entry, dict):
                _append_conf(entry.get("conf"))
                _append_conf(entry.get("confidence"))

    if not confidence_values:
        _append_conf(data.get("confidence"))

    avg_conf = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    return text, max(0.0, min(1.0, avg_conf))


class _HybridKaldiRecognizer:
    """Wrap multiple recognizers and merge their outputs."""

    def __init__(self, recognizers):
        self._recognizers = list(recognizers)

    def SetWords(self, enabled: bool) -> None:
        for rec in self._recognizers:
            setter = getattr(rec, "SetWords", None)
            if callable(setter):
                setter(enabled)

    def AcceptWaveform(self, chunk) -> bool:
        accepted = False
        for rec in self._recognizers:
            try:
                if rec.AcceptWaveform(chunk):
                    accepted = True
            except Exception:
                continue
        return accepted

    def Reset(self) -> None:
        for rec in self._recognizers:
            reset = getattr(rec, "Reset", None)
            if callable(reset):
                reset()

    def _collect(self, method: str, key: str) -> str:
        texts: list[str] = []
        best_text = ""
        best_conf = -1.0
        for rec in self._recognizers:
            try:
                payload = getattr(rec, method)()
            except Exception:
                continue
            text, conf = _parse_vosk_payload(payload, key)
            if text:
                texts.append(text)
            if conf > best_conf and text:
                best_conf = conf
                best_text = text
        if best_conf > 0:
            return best_text
        if best_text:
            return best_text
        return _merge_language_candidates(texts)

    def Result(self) -> str:
        return json.dumps({"text": self._collect("Result", "text")})

    def PartialResult(self) -> str:
        return json.dumps({"partial": self._collect("PartialResult", "partial")})

    def FinalResult(self) -> str:
        return json.dumps({"text": self._collect("FinalResult", "text")})


@dataclass(slots=True)
class MicrophoneSttOptions:
    model_path: str = ""
    model_paths: tuple[str, ...] = ()
    sample_rate: int = _DEFAULT_SAMPLE_RATE
    block_size: int = _DEFAULT_BLOCK_SIZE
    device: int | str | None = None
    emit_partial: bool = True
    auto_submit: bool = False
    auto_mode: bool = False
    silence_timeout_secs: float = _DEFAULT_SILENCE_TIMEOUT_SECS
    speech_rms_threshold: int = _DEFAULT_SPEECH_RMS_THRESHOLD
    denoise_enabled: bool = True
    denoise_strength: float = _DEFAULT_DENOISE_STRENGTH
    noise_gate_threshold: int = _DEFAULT_NOISE_GATE_THRESHOLD


class MicrophoneSttService:
    """Vosk 驱动的麦克风语音转文本服务。"""

    def __init__(self):
        self._ec = get_event_center()
        self._lock = threading.RLock()
        self._stream = None
        self._worker: threading.Thread | None = None
        self._audio_queue: queue.Queue[bytes | None] | None = None
        self._stop_event: threading.Event | None = None
        self._current_options = MicrophoneSttOptions()
        self._last_partial_text = ""
        self._auto_speech_active = False
        self._bootstrap_thread: Future | None = None
        self._bootstrap_cancel_event: threading.Event | None = None
        self._start_generation = 0

        self._ec.subscribe(EventType.MIC_STT_START, self._on_start_request)
        self._ec.subscribe(EventType.MIC_STT_STOP, self._on_stop_request)
        logger.info("[MicrophoneSttService] 已初始化")

    @property
    def is_listening(self) -> bool:
        with self._lock:
            return self._stream is not None and self._stop_event is not None and not self._stop_event.is_set()

    @property
    def auto_mode_enabled(self) -> bool:
        with self._lock:
            return bool(self.is_listening and self._current_options.auto_mode)

    def _on_start_request(self, event: Event):
        self.start_listening(event.data or {})
        event.mark_handled()

    def _on_stop_request(self, event: Event):
        self.stop_listening()
        event.mark_handled()

    def _publish_state(self, status: str, **extra) -> None:
        options = self._current_options
        payload = {
            "status": str(status or "idle"),
            "is_listening": self.is_listening,
            "auto_mode": bool(options.auto_mode and self.is_listening),
            "speech_active": bool(self._auto_speech_active),
            "auto_submit": bool(options.auto_submit),
        }
        payload.update(extra)
        self._ec.publish(Event(EventType.MIC_STT_STATE_CHANGE, payload))

    def _publish_info(self, text: str, *, min_tick: int = 16, max_tick: int = 120) -> None:
        self._ec.publish(Event(EventType.INFORMATION, {
            "text": str(text or "").strip(),
            "min": int(min_tick),
            "max": int(max_tick),
        }))

    def _publish_start_error(self, message: str, info_text: str | None = None) -> None:
        self._publish_state(
            "error",
            message=message,
            auto_mode=False,
            speech_active=False,
        )
        self._publish_info(info_text or message)

    def _cancel_bootstrap_locked(self, replacement_event: threading.Event | None = None) -> None:
        if self._bootstrap_cancel_event is not None:
            self._bootstrap_cancel_event.set()
        self._bootstrap_cancel_event = replacement_event

    @staticmethod
    def _queue_replace_nowait(target_queue: queue.Queue, item) -> None:
        try:
            target_queue.put_nowait(item)
        except queue.Full:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                target_queue.put_nowait(item)
            except queue.Full:
                pass

    @staticmethod
    def _close_stream_safely(stream) -> None:
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    @staticmethod
    def _start_failure_message(error: Exception) -> tuple[str, str]:
        detail = str(error)
        return (
            f"启动麦克风语音识别失败: {detail}",
            f"请检查麦克风或语音识别依赖是否可用: {detail}",
        )

    @staticmethod
    def _import_backend():
        try:
            import sounddevice as sd  # type: ignore
        except ImportError as e:
            return None, None, f"缺少依赖 sounddevice: {e}"

        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel  # type: ignore
        except ImportError as e:
            return None, None, f"缺少依赖 vosk: {e}"

        return sd, (Model, KaldiRecognizer, SetLogLevel), ""

    def _resolve_model_path(self, raw_path: str) -> Path | None:
        candidates: list[Path] = []

        if raw_path:
            raw_candidate = Path(os.path.expandvars(os.path.expanduser(raw_path)))
            if not raw_candidate.is_absolute():
                raw_candidate = _project_root() / raw_candidate
            candidates.append(raw_candidate)

        for env_key in _MODEL_ENV_KEYS:
            env_value = str(os.getenv(env_key, "") or "").strip()
            if env_value:
                candidates.append(Path(env_value))

        root_dir = _project_root()
        for relative_path in _MODEL_DIR_CANDIDATES:
            candidates.append(root_dir / relative_path)

        candidates.append(_shared_model_root())
        candidates.extend(_discover_bundled_model_dirs(root_dir))

        seen: set[str] = set()
        for candidate in candidates:
            try:
                normalized = str(candidate.resolve())
            except Exception:
                normalized = str(candidate)
            if normalized in seen:
                continue
            seen.add(normalized)
            if _is_valid_model_dir(candidate):
                return candidate
        if candidates:
            logger.warning(
                "[MicrophoneSttService] 未能在以下目录中找到有效的 Vosk 模型: %s",
                ", ".join(seen),
            )
        return None

    def _build_options(self, data: dict) -> MicrophoneSttOptions:
        raw_device = data.get("device")
        if isinstance(raw_device, str):
            raw_device = raw_device.strip() or None
        elif isinstance(raw_device, bool):
            raw_device = None

        default_model_path = str(_BUNDLED_MODEL_RELATIVE_PATH)
        default_silence_timeout = VOICE.get("microphone_silence_timeout_secs", _DEFAULT_SILENCE_TIMEOUT_SECS)
        default_speech_threshold = VOICE.get("microphone_speech_rms_threshold", _DEFAULT_SPEECH_RMS_THRESHOLD)
        default_denoise_enabled = VOICE.get("microphone_denoise_enabled", True)
        default_denoise_strength = VOICE.get("microphone_denoise_strength", _DEFAULT_DENOISE_STRENGTH)
        default_noise_gate = VOICE.get("microphone_noise_gate_threshold", _DEFAULT_NOISE_GATE_THRESHOLD)

        def _normalize_model_paths(value):
            if not value:
                return []
            if isinstance(value, (list, tuple)):
                raw_items = value
            else:
                raw_items = re.split(r"[;,]", str(value))
            normalized: list[str] = []
            for item in raw_items:
                item_text = str(item or "").strip()
                if not item_text:
                    continue
                if item_text not in normalized:
                    normalized.append(item_text)
            return normalized

        configured_paths = _normalize_model_paths(
            data.get("model_paths")
            or data.get("model_path")
            or VOICE.get("microphone_model_paths")
        )
        if not configured_paths:
            configured_paths = [default_model_path]

        return MicrophoneSttOptions(
            model_path=str(configured_paths[0]),
            model_paths=tuple(configured_paths),
            sample_rate=_coerce_int(data.get("sample_rate"), _DEFAULT_SAMPLE_RATE, 8000, 48000),
            block_size=_coerce_int(data.get("block_size"), _DEFAULT_BLOCK_SIZE, 800, 32000),
            device=raw_device,
            emit_partial=bool(data.get("emit_partial", True)),
            auto_submit=bool(data.get("auto_submit", False)),
            auto_mode=bool(data.get("auto_mode", False)),
            silence_timeout_secs=_coerce_float(data.get("silence_timeout_secs", default_silence_timeout), _DEFAULT_SILENCE_TIMEOUT_SECS, 0.5, 10.0),
            speech_rms_threshold=_coerce_int(data.get("speech_rms_threshold", default_speech_threshold), _DEFAULT_SPEECH_RMS_THRESHOLD, 50, 8000),
            denoise_enabled=bool(data.get("denoise_enabled", default_denoise_enabled)),
            denoise_strength=_coerce_float(data.get("denoise_strength", default_denoise_strength), _DEFAULT_DENOISE_STRENGTH, 0.0, 1.0),
            noise_gate_threshold=_coerce_int(data.get("noise_gate_threshold", default_noise_gate), _DEFAULT_NOISE_GATE_THRESHOLD, 0, 8000),
        )

    def _is_start_cancelled(self, start_generation: int, cancel_event: threading.Event | None = None) -> bool:
        if cancel_event is not None and cancel_event.is_set():
            return True
        with self._lock:
            return start_generation != self._start_generation

    def _clear_bootstrap_thread(self, start_generation: int, cancel_event: threading.Event | None) -> None:
        with self._lock:
            if start_generation == self._start_generation and self._bootstrap_cancel_event is cancel_event:
                self._bootstrap_thread = None
                self._bootstrap_cancel_event = None

    def start_listening(self, data: dict | None = None) -> bool:
        options = self._build_options(data or {})
        cancel_event = threading.Event()

        with self._lock:
            self._start_generation += 1
            start_generation = self._start_generation
            self._cancel_bootstrap_locked(cancel_event)
            self._current_options = options
            self._last_partial_text = ""
            self._auto_speech_active = False
            self._bootstrap_thread = get_compute_hub().submit_interactive_io(
                self._bootstrap_startup,
                start_generation,
                options,
                cancel_event,
            )

        self._publish_state(
            "starting",
            message="正在启动麦克风语音识别...",
            auto_mode=bool(options.auto_mode),
            speech_active=False,
            auto_submit=bool(options.auto_submit),
        )
        return True

    def _bootstrap_startup(
        self,
        start_generation: int,
        options: MicrophoneSttOptions,
        cancel_event: threading.Event,
    ) -> None:
        try:
            sd, backend, import_error = self._import_backend()
            if self._is_start_cancelled(start_generation, cancel_event):
                return

            if import_error:
                logger.warning("[MicrophoneSttService] %s", import_error)
                self._publish_start_error(import_error, f"无法启动语音识别: {import_error}")
                return

            resolved_model_paths: list[Path] = []
            missing_models: list[str] = []
            for raw_path in options.model_paths:
                if self._is_start_cancelled(start_generation, cancel_event):
                    return
                resolved = self._resolve_model_path(raw_path)
                if resolved is None:
                    missing_models.append(str(raw_path))
                    continue
                resolved_model_paths.append(resolved)

            if self._is_start_cancelled(start_generation, cancel_event):
                return

            if not resolved_model_paths:
                message = "未找到可用的 Vosk 模型，请检查 microphone_model_paths 配置"
                logger.warning("[MicrophoneSttService] %s", message)
                self._publish_start_error(message, "未能加载任何 Vosk 模型，请确认模型目录是否存在")
                return

            Model, KaldiRecognizer, SetLogLevel = backend
            try:
                SetLogLevel(-1)
            except Exception:
                pass

            models_info: list[dict] = []
            for model_path in resolved_model_paths:
                load_path, model_arg = _resolve_model_load_arg(model_path)
                if model_arg != str(model_path):
                    logger.info("[MicrophoneSttService] 模型路径已重写: source=%s load=%s", model_path, model_arg)
                try:
                    model_obj = Model(model_arg)
                except Exception as e:
                    logger.error("[MicrophoneSttService] 加载 Vosk 模型失败: %s (source=%s, load=%s)", e, model_path, model_arg)
                    continue
                models_info.append({
                    "source": model_path,
                    "load": load_path,
                    "model": model_obj,
                })

            if self._is_start_cancelled(start_generation, cancel_event):
                return

            if not models_info:
                message = "所有配置的 Vosk 模型均加载失败"
                self._publish_start_error(message, "请检查模型目录是否完整或权限可访问")
                return

            if missing_models:
                logger.warning("[MicrophoneSttService] 以下模型目录未找到：%s", ", ".join(missing_models))

            logger.info(
                "[MicrophoneSttService] 已加载 %d 个模型: %s",
                len(models_info),
                ", ".join(str(info["load"]) for info in models_info),
            )

            def build_recognizer():
                recognizers = []
                for info in models_info:
                    recognizer = KaldiRecognizer(info["model"], float(options.sample_rate))
                    if hasattr(recognizer, "SetWords"):
                        recognizer.SetWords(True)
                    recognizers.append(recognizer)
                if len(recognizers) == 1:
                    return recognizers[0]
                return _HybridKaldiRecognizer(recognizers)

            worker = threading.Thread(
                target=self._worker_loop,
                args=(build_recognizer, options),
                daemon=True,
                name="microphone-stt-worker",
            )

            try:
                stream = sd.RawInputStream(
                    samplerate=options.sample_rate,
                    blocksize=options.block_size,
                    device=options.device,
                    dtype=_DEFAULT_DTYPE,
                    channels=_DEFAULT_CHANNELS,
                    callback=self._on_audio_chunk,
                )
            except Exception as e:
                if self._is_start_cancelled(start_generation, cancel_event):
                    return
                logger.error("[MicrophoneSttService] 创建音频输入流失败: %s", e)
                message, info_text = self._start_failure_message(e)
                self._publish_start_error(message, info_text)
                return

            with self._lock:
                if self._is_start_cancelled(start_generation, cancel_event):
                    self._close_stream_safely(stream)
                    return

                self._stop_locked(join_worker=True, emit_state=False)
                self._audio_queue = queue.Queue(maxsize=_DEFAULT_QUEUE_SIZE)
                self._stop_event = threading.Event()
                self._last_partial_text = ""
                self._auto_speech_active = False
                self._current_options = options
                self._stream = stream

            try:
                worker.start()
                with self._lock:
                    if self._stream is stream:
                        self._worker = worker
                stream.start()
            except Exception as e:
                with self._lock:
                    self._stop_locked(join_worker=True, emit_state=False)
                if self._is_start_cancelled(start_generation, cancel_event):
                    return
                logger.error("[MicrophoneSttService] 启动音频输入流失败: %s", e)
                message, info_text = self._start_failure_message(e)
                self._publish_start_error(message, info_text)
                return

            if self._is_start_cancelled(start_generation, cancel_event):
                with self._lock:
                    self._stop_locked(join_worker=True, emit_state=False)
                return

            model_sources = [str(info["source"]) for info in models_info]
            model_summary = ", ".join(model_sources)

            logger.info(
                "[MicrophoneSttService] 已开始监听 model=%s sample_rate=%s device=%r auto_mode=%s",
                model_summary,
                options.sample_rate,
                options.device,
                options.auto_mode,
            )
            if options.auto_mode:
                self._publish_state(
                    "monitoring",
                    message="自动语聊监听中，等待语音触发",
                    model_path=model_summary,
                    model_paths=model_sources,
                    sample_rate=options.sample_rate,
                    device=options.device,
                )
            else:
                self._publish_state(
                    "running",
                    message="语音识别已启动",
                    model_path=model_summary,
                    model_paths=model_sources,
                    sample_rate=options.sample_rate,
                    device=options.device,
                )
        finally:
            self._clear_bootstrap_thread(start_generation, cancel_event)

    def _on_audio_chunk(self, indata, frames, time_info, status) -> None:
        del frames, time_info
        if status:
            logger.debug("[MicrophoneSttService] 输入流状态: %s", status)

        audio_queue = self._audio_queue
        stop_event = self._stop_event
        if audio_queue is None or stop_event is None or stop_event.is_set():
            return

        chunk = bytes(indata)
        self._queue_replace_nowait(audio_queue, chunk)

    @staticmethod
    def _chunk_rms(chunk: bytes) -> int:
        if not chunk:
            return 0
        try:
            samples = array.array('h')
            samples.frombytes(chunk)
            if not samples:
                return 0
            square_sum = 0
            for sample in samples:
                square_sum += int(sample) * int(sample)
            return int(math.sqrt(square_sum / len(samples)))
        except Exception:
            return 0

    def _publish_partial_text(self, text: str) -> None:
        normalized = _normalize_text(text)
        if not normalized or normalized == self._last_partial_text:
            return
        self._last_partial_text = normalized
        logger.debug("[MicrophoneSttService] Partial text: %s", normalized)
        self._ec.publish(Event(EventType.MIC_STT_PARTIAL, {
            "text": normalized,
            "source": "microphone_stt",
        }))

    def _worker_loop(self, recognizer_factory, options: MicrophoneSttOptions) -> None:
        logger.info("[MicrophoneSttService] 后台识别线程已启动")
        recognizer = None if options.auto_mode else recognizer_factory()
        auto_segments: list[str] = []
        last_voice_time = 0.0
        noise_floor: float | None = None

        try:
            while True:
                audio_queue = self._audio_queue
                stop_event = self._stop_event
                if audio_queue is None or stop_event is None:
                    break

                try:
                    chunk = audio_queue.get(timeout=0.2)
                except queue.Empty:
                    if stop_event.is_set():
                        break
                    if options.auto_mode and self._auto_speech_active and last_voice_time > 0.0:
                        if time.monotonic() - last_voice_time >= options.silence_timeout_secs:
                            self._finalize_auto_session(recognizer, auto_segments, options)
                            recognizer = None
                            auto_segments = []
                    continue

                if chunk is None:
                    break

                if options.denoise_enabled:
                    chunk, noise_floor = denoise_pcm16(
                        chunk,
                        strength=options.denoise_strength,
                        gate_threshold=options.noise_gate_threshold,
                        noise_floor=noise_floor,
                    )

                if options.auto_mode:
                    recognizer, auto_segments, last_voice_time = self._handle_auto_mode_chunk(
                        recognizer_factory,
                        recognizer,
                        auto_segments,
                        chunk,
                        last_voice_time,
                        options,
                    )
                    continue

                if recognizer is None:
                    recognizer = recognizer_factory()
                accepted = recognizer.AcceptWaveform(chunk)
                if accepted:
                    final_text = _extract_text(recognizer.Result(), "text")
                    if final_text:
                        self._emit_final_text(final_text, options)
                elif options.emit_partial:
                    self._publish_partial_text(_extract_text(recognizer.PartialResult(), "partial"))
        except Exception as e:
            logger.error("[MicrophoneSttService] 后台识别线程异常: %s", e)
            self._publish_state("error", message=f"后台识别线程异常: {e}")
        finally:
            if options.auto_mode:
                self._finalize_auto_session(recognizer, auto_segments, options)
            else:
                try:
                    final_text = _extract_text(recognizer.FinalResult(), "text") if recognizer is not None else ""
                except Exception:
                    final_text = ""
                if final_text:
                    self._emit_final_text(final_text, options)
            self._auto_speech_active = False
            logger.info("[MicrophoneSttService] 后台识别线程已结束")

    def _handle_auto_mode_chunk(
        self,
        recognizer_factory,
        recognizer,
        auto_segments: list[str],
        chunk: bytes,
        last_voice_time: float,
        options: MicrophoneSttOptions,
    ):
        now = time.monotonic()
        chunk_rms = self._chunk_rms(chunk)
        is_speaking = chunk_rms >= options.speech_rms_threshold

        if recognizer is None and not is_speaking:
            return None, auto_segments, last_voice_time

        if recognizer is None:
            recognizer = recognizer_factory()
            auto_segments = []
            self._last_partial_text = ""
            self._auto_speech_active = True
            last_voice_time = now
            self._publish_state("capturing", message="检测到说话，开始语音转文字", speech_active=True)

        if is_speaking:
            last_voice_time = now

        accepted = recognizer.AcceptWaveform(chunk)
        if accepted:
            text = _extract_text(recognizer.Result(), "text")
            if text:
                auto_segments.append(text)
                self._last_partial_text = ""
        elif options.emit_partial:
            self._publish_partial_text(_extract_text(recognizer.PartialResult(), "partial"))

        if last_voice_time > 0.0 and now - last_voice_time >= options.silence_timeout_secs:
            self._finalize_auto_session(recognizer, auto_segments, options)
            return None, [], 0.0

        return recognizer, auto_segments, last_voice_time

    def _finalize_auto_session(self, recognizer, auto_segments: list[str], options: MicrophoneSttOptions) -> None:
        if recognizer is None:
            if self._auto_speech_active:
                self._auto_speech_active = False
                self._last_partial_text = ""
                self._publish_state("monitoring", message="自动语聊监听中", speech_active=False)
            return

        try:
            final_text = _extract_text(recognizer.FinalResult(), "text")
        except Exception:
            final_text = ""

        merged_text = _merge_text_segments(*(auto_segments + [final_text]))
        self._last_partial_text = ""
        if merged_text:
            self._emit_final_text(merged_text, options)

        self._auto_speech_active = False
        if self.is_listening and options.auto_mode:
            self._publish_state("monitoring", message="自动语聊监听中", speech_active=False)

    def _emit_final_text(self, text: str, options: MicrophoneSttOptions) -> None:
        normalized = _normalize_text(text)
        if not normalized:
            return

        self._last_partial_text = ""
        logger.info("[MicrophoneSttService] Final text (auto=%s): %s", options.auto_mode, normalized)
        payload = {
            "text": normalized,
            "source": "microphone_stt",
            "auto_submit": bool(options.auto_submit),
        }
        self._ec.publish(Event(EventType.MIC_STT_FINAL, payload))
        if options.auto_submit:
            self._ec.publish(Event(EventType.INPUT_CHAT, {
                "text": normalized,
                "raw": normalized,
                "source": "microphone_stt",
            }))

    def stop_listening(self) -> None:
        should_publish_stopped = False
        with self._lock:
            was_starting = self._bootstrap_thread is not None and not self._bootstrap_thread.done()
            was_running = self._stream is not None or self._worker is not None

            self._start_generation += 1
            self._cancel_bootstrap_locked()
            self._bootstrap_thread = None

            self._stop_locked(join_worker=True, emit_state=True)
            should_publish_stopped = bool(was_starting and not was_running)

        if should_publish_stopped:
            self._publish_state("stopped", message="已停止监听", auto_mode=False, speech_active=False)

    def _stop_locked(self, *, join_worker: bool, emit_state: bool) -> None:
        worker = self._worker
        stream = self._stream
        audio_queue = self._audio_queue
        stop_event = self._stop_event
        was_running = stream is not None or worker is not None

        if stop_event is not None:
            stop_event.set()

        self._close_stream_safely(stream)

        if audio_queue is not None:
            self._queue_replace_nowait(audio_queue, None)

        self._stream = None
        self._worker = None
        self._audio_queue = None
        self._stop_event = None
        self._current_options = MicrophoneSttOptions()
        self._last_partial_text = ""
        self._auto_speech_active = False

        if join_worker and worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.5)

        if emit_state and was_running:
            logger.info("[MicrophoneSttService] 已停止监听")
            self._publish_state("stopped", message="已停止监听", auto_mode=False, speech_active=False)

    def cleanup(self) -> None:
        self.stop_listening()
        self._ec.unsubscribe(EventType.MIC_STT_START, self._on_start_request)
        self._ec.unsubscribe(EventType.MIC_STT_STOP, self._on_stop_request)


_instance: MicrophoneSttService | None = None


def get_microphone_stt_service() -> MicrophoneSttService:
    global _instance
    if _instance is None:
        _instance = MicrophoneSttService()
    return _instance


def cleanup_microphone_stt_service():
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
