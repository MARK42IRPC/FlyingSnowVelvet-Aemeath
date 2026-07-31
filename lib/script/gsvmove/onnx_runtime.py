"""Small adapter around the inference entry shipped in an ONNX voice package."""

from __future__ import annotations

import gc
import importlib.util
import math
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from lib.script.gsvmove.package_manager import validate_voice_package


_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_LANGUAGE_TOKEN_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z]+|[^A-Za-z\u3400-\u4dbf\u4e00-\u9fff]+"
)


class OnnxVoiceRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class OnnxInferenceRequest:
    text: str
    language: str
    prompt_text: str | None
    prompt_language: str
    top_k: int
    top_p: float
    speed_factor: float
    temperature: float
    text_split_method: str
    batch_size: int
    batch_threshold: float
    split_bucket: bool
    fragment_interval: float
    seed: int
    media_type: str
    parallel_infer: bool
    repetition_penalty: float
    sample_steps: int
    super_sampling: bool
    streaming_mode: int
    overlap_length: int
    min_chunk_length: int
    max_steps: int

    @classmethod
    def from_payload(cls, payload: dict) -> "OnnxInferenceRequest":
        text = str(payload.get("text") or "").strip()
        if not text:
            raise OnnxVoiceRuntimeError("语音文本为空")

        language = normalize_language(
            payload.get("text_lang", payload.get("text_language", payload.get("language"))),
            text,
        )
        prompt_text = str(payload.get("prompt_text") or "").strip() or None
        prompt_language = normalize_language(
            payload.get("prompt_lang", payload.get("prompt_language", "zh")),
            prompt_text or "中文",
        )
        top_k = _bounded_int(payload.get("top_k"), 15, 1, 1025)
        top_p = _bounded_float(payload.get("top_p"), 1.0, 0.01, 1.0)
        speed_factor = _bounded_float(payload.get("speed_factor"), 1.0, 0.5, 2.0)
        temperature = _bounded_float(payload.get("temperature"), 1.0, 0.01, 2.0)
        text_split_method = str(payload.get("text_split_method") or "cut5").strip().lower()
        if text_split_method not in {"cut0", "cut1", "cut2", "cut3", "cut4", "cut5"}:
            text_split_method = "cut5"
        batch_size = _bounded_int(payload.get("batch_size"), 1, 1, 200)
        batch_threshold = _bounded_float(payload.get("batch_threshold"), 0.75, 0.0, 1.0)
        split_bucket = _coerce_bool(payload.get("split_bucket"), True)
        fragment_interval = _bounded_float(payload.get("fragment_interval"), 0.3, 0.0, 5.0)
        seed = _bounded_int(payload.get("seed"), -1, -1, 2**32 - 1)
        media_type = str(payload.get("media_type") or "wav").strip().lower()
        if media_type not in {"wav", "raw", "ogg", "aac"}:
            media_type = "wav"
        parallel_infer = _coerce_bool(payload.get("parallel_infer"), True)
        repetition_penalty = _bounded_float(payload.get("repetition_penalty"), 1.35, 0.1, 2.0)
        sample_steps = _bounded_int(payload.get("sample_steps"), 32, 1, 1000)
        super_sampling = _coerce_bool(payload.get("super_sampling"), False)
        streaming_mode = _bounded_int(payload.get("streaming_mode"), 0, 0, 3)
        overlap_length = _bounded_int(payload.get("overlap_length"), 2, 0, 128)
        min_chunk_length = _bounded_int(payload.get("min_chunk_length"), 16, 1, 1024)
        max_steps = _bounded_int(payload.get("max_steps"), 500, 64, 1200)
        return cls(
            text=text,
            language=language,
            prompt_text=prompt_text,
            prompt_language=prompt_language,
            top_k=top_k,
            top_p=top_p,
            speed_factor=speed_factor,
            temperature=temperature,
            text_split_method=text_split_method,
            batch_size=batch_size,
            batch_threshold=batch_threshold,
            split_bucket=split_bucket,
            fragment_interval=fragment_interval,
            seed=seed,
            media_type=media_type,
            parallel_infer=parallel_infer,
            repetition_penalty=repetition_penalty,
            sample_steps=sample_steps,
            super_sampling=super_sampling,
            streaming_mode=streaming_mode,
            overlap_length=overlap_length,
            min_chunk_length=min_chunk_length,
            max_steps=max_steps,
        )


def _bounded_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _coerce_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "off", "no"}
    return bool(value)


def normalize_language(value, text: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in {"zh", "zh-cn", "zh-hans", "chinese", "all-zh"}:
        return "zh"
    if normalized in {"en", "en-us", "en-gb", "english", "all-en"}:
        return "en"
    has_cjk = bool(_CJK_PATTERN.search(text))
    has_latin = bool(_LATIN_PATTERN.search(text))
    if has_cjk and has_latin:
        return "auto"
    if has_cjk:
        return "zh"
    if has_latin:
        return "en"
    return "zh"


def _split_auto_language_text(text: str) -> tuple[tuple[str, str], ...]:
    """Split mixed CJK/Latin text without dropping punctuation or whitespace."""
    source = str(text or "")
    if not source:
        return ()

    segments: list[tuple[str, str]] = []
    current_language: str | None = None
    current_parts: list[str] = []
    prefix = ""

    for match in _LANGUAGE_TOKEN_PATTERN.finditer(source):
        token = match.group(0)
        if _CJK_PATTERN.search(token):
            token_language = "zh"
        elif _LATIN_PATTERN.search(token):
            token_language = "en"
        else:
            if current_language is None:
                prefix += token
            else:
                current_parts.append(token)
            continue

        if current_language is None:
            current_language = token_language
            current_parts = [prefix, token]
            prefix = ""
        elif current_language == token_language:
            current_parts.append(token)
        else:
            segments.append(("".join(current_parts), current_language))
            current_language = token_language
            current_parts = [token]

    if current_language is not None:
        current_parts.append(prefix)
        segments.append(("".join(current_parts), current_language))
        return tuple((value, language) for value, language in segments if value)

    # Keep punctuation- and number-only inputs available to the Chinese frontend.
    return ((source, "zh"),)


class OnnxVoiceRuntime:
    """Load one package engine and reuse it for all synthesis requests."""

    def __init__(self, package_root: Path, *, provider: str = "cpu") -> None:
        root = Path(package_root)
        validation = validate_voice_package(root)
        if not validation.valid or validation.manifest is None:
            raise OnnxVoiceRuntimeError(validation.reason)

        infer_path = root / "infer.py"
        module_name = f"_aemeath_voice_infer_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, infer_path)
        if spec is None or spec.loader is None:
            raise OnnxVoiceRuntimeError("无法加载语音包推理入口")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            providers = module.select_providers(provider)
            engine = module.AimisiOnnx(root, providers)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            raise OnnxVoiceRuntimeError(f"ONNX 语音模型加载失败：{exc}") from exc

        self.package_root = root
        self.sample_rate = int(validation.manifest.get("sample_rate") or 32000)
        self._module_name = module_name
        self._module = module
        self._engine = engine

    def synthesize_to_file(self, payload: dict, output_path: Path) -> Path:
        request = OnnxInferenceRequest.from_payload(payload)
        try:
            audio = self._synthesize_audio(request)
            destination = Path(output_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._module.sf.write(
                destination,
                audio,
                self.sample_rate,
                subtype="PCM_16",
            )
            if not destination.is_file() or destination.stat().st_size <= 44:
                raise OnnxVoiceRuntimeError("ONNX 推理没有生成有效 WAV 文件")
            return destination
        except OnnxVoiceRuntimeError:
            raise
        except Exception as exc:
            raise OnnxVoiceRuntimeError(f"ONNX 语音推理失败：{exc}") from exc

    def _synthesize_audio(self, request: OnnxInferenceRequest) -> np.ndarray:
        engine = self._engine
        if engine is None:
            raise OnnxVoiceRuntimeError("ONNX 语音引擎未就绪")

        segments = (
            _split_auto_language_text(request.text)
            if request.language == "auto"
            else ((request.text, request.language),)
        )
        chunks: list[np.ndarray] = []
        silence = np.zeros(
            round(self.sample_rate * request.fragment_interval),
            dtype=np.float32,
        )
        for index, (text, language) in enumerate(segments):
            audio = engine.synthesize(
                text,
                language,
                max_steps=request.max_steps,
                prompt_text=request.prompt_text,
                prompt_lang=request.prompt_language,
                top_k=request.top_k,
                top_p=request.top_p,
                temperature=request.temperature,
                text_split_method=request.text_split_method,
                batch_size=request.batch_size,
                batch_threshold=request.batch_threshold,
                split_bucket=request.split_bucket,
                speed_factor=request.speed_factor,
                fragment_interval=request.fragment_interval,
                seed=request.seed,
                parallel_infer=request.parallel_infer,
                repetition_penalty=request.repetition_penalty,
                sample_steps=request.sample_steps,
                super_sampling=request.super_sampling,
                streaming_mode=request.streaming_mode,
                overlap_length=request.overlap_length,
                min_chunk_length=request.min_chunk_length,
            )
            chunks.append(np.asarray(audio, dtype=np.float32).reshape(-1))
            if silence.size and index + 1 < len(segments):
                chunks.append(silence)

        if not chunks:
            raise OnnxVoiceRuntimeError("语音文本不包含可合成片段")
        return np.concatenate(chunks)

    def close(self) -> None:
        self._engine = None
        self._module = None
        sys.modules.pop(self._module_name, None)
        gc.collect()
