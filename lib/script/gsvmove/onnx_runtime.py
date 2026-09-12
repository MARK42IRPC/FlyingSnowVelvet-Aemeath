"""Small adapter around the inference entry shipped in an ONNX voice package."""

from __future__ import annotations

import gc
import importlib
import importlib.machinery
import importlib.util
import math
import os
import re
import sys
import types
import uuid
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path

import numpy as np

from lib.core.logger import get_logger
from lib.script.gsvmove.package_manager import validate_voice_package
from lib.script.gsvmove.native_graph import (
    NativeGraphSession,
    NativeRuntimeUnavailable,
    native_device_count,
    native_last_error,
)


_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_LANGUAGE_TOKEN_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z]+|[^A-Za-z\u3400-\u4dbf\u4e00-\u9fff]+"
)


class OnnxVoiceRuntimeError(RuntimeError):
    pass


_HYBRID_CPU_MODEL_NAMES = {"t2s_stage_decoder_fp32.onnx"}
GENIE_TTS_REQUIRED_VERSION = "2.0.2"
_GENIE_TTS_MODULE = "genie_tts"
_GENIE_TTS_REQUIRED_FILES = (
    "GetPhonesAndBert.py",
    "ModelManager.py",
    "Core/Resources.py",
    "G2P/SymbolsV2.py",
    "G2P/Chinese/ChineseG2P.py",
    "G2P/English/EnglishG2P.py",
)
_GENIE_RESOURCE_ENV = (
    "GENIE_DATA_DIR",
    "English_G2P_DIR",
    "Chinese_G2P_DIR",
    "HUBERT_MODEL_DIR",
    "SV_MODEL",
    "ROBERTA_MODEL_DIR",
)


def _normalise_distribution_name(value: object) -> str:
    return re.sub(r"[-_.]+", "-", str(value or "").strip()).casefold()


def _python_site_package_roots() -> tuple[Path, ...]:
    """Return site-package roots belonging to this interpreter first.

    ``-I`` removes PYTHONPATH and user-site entries, but an application started
    from a regular interpreter can still inherit arbitrary ``sys.path`` items.
    Prefer the interpreter's own installation so an unrelated genie_tts package
    cannot silently win package discovery.
    """
    roots: list[Path] = []
    prefixes = (getattr(sys, "prefix", ""), getattr(sys, "base_prefix", ""))
    executable = Path(sys.executable or "")
    if executable.name:
        prefixes += (str(executable.parent), str(executable.parent.parent))
    for prefix in prefixes:
        if not prefix:
            continue
        base = Path(prefix)
        candidates = (base / "Lib" / "site-packages", base / "lib" / "site-packages")
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if resolved.is_dir() and resolved not in roots:
                roots.append(resolved)
    return tuple(roots)


def _genie_search_roots() -> tuple[Path, ...]:
    roots: list[Path] = list(_python_site_package_roots())
    for item in sys.path:
        if not item:
            continue
        try:
            candidate = Path(item).resolve()
        except (OSError, TypeError, ValueError):
            continue
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return tuple(roots)


def _read_genie_distribution(parent: Path) -> tuple[str | None, str | None, Path | None]:
    """Read genie-tts metadata without importing the package."""
    try:
        candidates = sorted(parent.glob("genie_tts-*.dist-info"), key=lambda item: item.name.casefold())
    except OSError:
        candidates = []
    for directory in candidates:
        metadata_path = directory / "METADATA"
        try:
            message = Parser().parsestr(metadata_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        name = str(message.get("Name") or "").strip()
        version = str(message.get("Version") or "").strip()
        if _normalise_distribution_name(name) == "genie-tts":
            return name, version, directory
    return None, None, None


def _find_genie_tts_package() -> tuple[Path, str, Path]:
    """Find a complete, pinned Genie package using the current interpreter."""
    seen: set[str] = set()
    diagnostics: list[str] = []
    for parent in _genie_search_roots():
        key = os.path.normcase(os.path.abspath(str(parent)))
        if key in seen:
            continue
        seen.add(key)
        try:
            spec = importlib.machinery.PathFinder.find_spec(_GENIE_TTS_MODULE, [str(parent)])
        except (ImportError, OSError, AttributeError):
            spec = None
        locations = tuple(spec.submodule_search_locations or ()) if spec is not None else ()
        if not locations:
            continue
        package_root = Path(locations[0]).resolve()
        name, version, metadata_dir = _read_genie_distribution(parent)
        if name is None:
            diagnostics.append(f"{package_root} (缺少 genie-tts METADATA)")
            continue
        if version != GENIE_TTS_REQUIRED_VERSION:
            diagnostics.append(f"{package_root} ({name} {version or 'unknown'})")
            continue
        missing = [item for item in _GENIE_TTS_REQUIRED_FILES if not (package_root / Path(item)).is_file()]
        if missing:
            diagnostics.append(f"{package_root} (缺少 {missing[0]})")
            continue
        return package_root, version, metadata_dir  # type: ignore[return-value]
    detail = "; ".join(diagnostics[:4]) or "未找到可搜索的 site-packages"
    raise OnnxVoiceRuntimeError(
        "genie-tts==2.0.2 双语文本前端不可用；"
        f"解释器={sys.executable!r}；检查结果={detail}"
    )


def _make_genie_package_module(package_root: Path) -> types.ModuleType:
    """Install a package shell without executing its GUI-heavy __init__.py."""
    package_spec = importlib.machinery.ModuleSpec(
        _GENIE_TTS_MODULE,
        loader=None,
        is_package=True,
    )
    package_spec.submodule_search_locations = [str(package_root)]
    package = types.ModuleType(_GENIE_TTS_MODULE)
    package.__file__ = str(package_root / "__init__.py")
    package.__path__ = [str(package_root)]  # type: ignore[attr-defined]
    package.__package__ = _GENIE_TTS_MODULE
    package.__spec__ = package_spec
    package.__version__ = GENIE_TTS_REQUIRED_VERSION
    return package


def _load_isolated_genie_frontend(common_dir: Path):
    """Load only Genie's bilingual frontend and local resources.

    The published ``genie-tts`` top-level module starts a GUI/server import and
    is not safe in a headless installer/runtime.  A synthetic package shell
    lets the two frontend modules use their normal relative imports while
    avoiding that side effect entirely.
    """
    previous_modules = {
        name: value
        for name, value in sys.modules.items()
        if name == _GENIE_TTS_MODULE or name.startswith(_GENIE_TTS_MODULE + ".")
    }
    previous_environment = {name: os.environ.get(name) for name in _GENIE_RESOURCE_ENV}
    root = Path(common_dir).resolve()
    resource_values = {
        "GENIE_DATA_DIR": root,
        "English_G2P_DIR": root / "G2P" / "EnglishG2P",
        "Chinese_G2P_DIR": root / "G2P" / "ChineseG2P",
        "HUBERT_MODEL_DIR": root / "chinese-hubert-base",
        "SV_MODEL": root / "speaker_encoder.onnx",
        "ROBERTA_MODEL_DIR": root / "RoBERTa",
    }
    required_resources = (
        resource_values["English_G2P_DIR"] / "checkpoint20.npz",
        resource_values["Chinese_G2P_DIR"] / "opencpop-strict.txt",
        resource_values["HUBERT_MODEL_DIR"] / "chinese-hubert-base.onnx",
        resource_values["SV_MODEL"],
        resource_values["ROBERTA_MODEL_DIR"] / "RoBERTa.onnx",
        resource_values["ROBERTA_MODEL_DIR"] / "roberta_tokenizer" / "tokenizer.json",
    )
    missing_resources = [str(path) for path in required_resources if not path.is_file()]
    if missing_resources:
        raise OnnxVoiceRuntimeError(
            "语音包缺少 Genie 本地资源：" + ", ".join(missing_resources[:3])
        )
    try:
        package_root, _version, _metadata_dir = _find_genie_tts_package()
        for name in tuple(sys.modules):
            if name == _GENIE_TTS_MODULE or name.startswith(_GENIE_TTS_MODULE + "."):
                sys.modules.pop(name, None)
        for name, value in resource_values.items():
            os.environ[name] = str(value)
        sys.modules[_GENIE_TTS_MODULE] = _make_genie_package_module(package_root)
        frontend_module = importlib.import_module("genie_tts.GetPhonesAndBert")
        manager_module = importlib.import_module("genie_tts.ModelManager")
        g2pw_path = root / "G2P" / "G2PW" / "g2pw_frontend.py"
        if g2pw_path.is_file():
            adapter_spec = importlib.util.spec_from_file_location("aemeath_g2pw_frontend", g2pw_path)
            if adapter_spec is None or adapter_spec.loader is None:
                raise RuntimeError("无法初始化内置 G2PW 前端")
            adapter = importlib.util.module_from_spec(adapter_spec)
            adapter_spec.loader.exec_module(adapter)
            install = getattr(adapter, "install", None)
            if callable(install):
                install(root)
        model_manager = getattr(manager_module, "model_manager", None)
        if model_manager is None or not callable(getattr(model_manager, "load_roberta_model", None)):
            raise RuntimeError("Genie ModelManager 不完整")
        if not model_manager.load_roberta_model():
            raise RuntimeError("内置 Chinese RoBERTa 模型无法加载")
        get_phones = getattr(frontend_module, "get_phones_and_bert", None)
        if not callable(get_phones):
            raise RuntimeError("Genie 双语前端缺少 get_phones_and_bert")
    except Exception:
        for name in tuple(sys.modules):
            if name == _GENIE_TTS_MODULE or name.startswith(_GENIE_TTS_MODULE + "."):
                sys.modules.pop(name, None)
        sys.modules.update(previous_modules)
        raise
    finally:
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def cleanup() -> None:
        for name in tuple(sys.modules):
            if name == _GENIE_TTS_MODULE or name.startswith(_GENIE_TTS_MODULE + "."):
                sys.modules.pop(name, None)
        sys.modules.update(previous_modules)

    return get_phones, cleanup


def _configure_native_cuda_sessions(module) -> list[NativeGraphSession]:
    """Route the archive's graphs through the self-written CUDA runtime.

    The archive keeps owning everything that is not a graph forward pass: text
    normalisation, the Chinese frontend and the sampling loop all stay in the
    package's Python.  Every acoustic graph (hubert, speaker, both T2S decoders
    and VITS) is created through ``load_optional_external_session``, so replacing
    that single seam moves the whole synthesis chain onto the GPU.
    """

    count = native_device_count()
    if count <= 0:
        detail = native_last_error()
        suffix = f"：{detail}" if detail else ""
        raise OnnxVoiceRuntimeError(f"自研 CUDA 推理端没有可用设备{suffix}")

    native_sessions: list[NativeGraphSession] = []

    def load_native_session(model_path, weights_path, _providers):
        try:
            session = NativeGraphSession(model_path, weights_path)
        except NativeRuntimeUnavailable as exc:
            raise OnnxVoiceRuntimeError(str(exc)) from exc
        except Exception as exc:
            raise OnnxVoiceRuntimeError(
                f"自研 CUDA 推理端无法加载 {Path(model_path).name}：{exc}"
            ) from exc
        native_sessions.append(session)
        return session

    module.load_optional_external_session = load_native_session
    return native_sessions


def _release_native_sessions(sessions) -> None:
    for session in reversed(tuple(sessions or ())):
        try:
            session.close()
        except Exception:
            pass
    sessions.clear()


def _configure_hybrid_provider(module) -> list[str]:
    """Use DirectML for throughput graphs and CPU for iterative T2S decoding."""
    available = set(module.ort.get_available_providers())
    if "DmlExecutionProvider" not in available:
        raise OnnxVoiceRuntimeError(
            f"DirectML Provider 不可用，当前 Provider：{sorted(available)}"
        )
    if "CPUExecutionProvider" not in available:
        raise OnnxVoiceRuntimeError("CPU fallback Provider 不可用")

    def make_session_options():
        options = module.ort.SessionOptions()
        options.graph_optimization_level = module.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.execution_mode = module.ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_mem_pattern = False
        options.intra_op_num_threads = max(1, min(module.os.cpu_count() or 1, 8))
        return options

    original_loader = module.load_optional_external_session

    def load_hybrid_session(model_path, weights_path, _providers):
        model_name = Path(model_path).name
        providers = (
            ["CPUExecutionProvider"]
            if model_name in _HYBRID_CPU_MODEL_NAMES
            else ["DmlExecutionProvider", "CPUExecutionProvider"]
        )
        return original_loader(model_path, weights_path, providers)

    module.make_session_options = make_session_options
    module.load_optional_external_session = load_hybrid_session
    return ["DmlExecutionProvider", "CPUExecutionProvider"]


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


# One synthesis request shares a single ``max_steps`` semantic-decode budget,
# and the packed archive cuts the audio silently when that budget runs out:
# the decoder never reports ``stop_condition`` and only prints a warning to
# stdout, which the worker keeps off the protocol channel.  Measured on the
# pinned v2Pro package (speed_factor 1.1) one Chinese character costs 4.5-5.2
# semantic tokens, so the default 500 steps only covers about 100 characters
# and ``cut0`` requests dropped the tail of longer replies.  Budget every
# request against the cap instead of trusting the split method to do it.
_SEMANTIC_TOKENS_PER_CHAR = 6.0
_SEMANTIC_BUDGET_RATIO = 0.8
_SEMANTIC_MIN_BUDGET_CHARS = 24
# A healthy segment speaks roughly 0.19 s per character at speed 1.1; anything
# far below this floor means the decode stopped before the text was finished.
_SEMANTIC_MIN_SECONDS_PER_CHAR = 0.05
_SEMANTIC_CLAUSE_PATTERN = re.compile(
    r"[^。！？!?…；;，,、：:.\n]+[。！？!?…；;，,、：:.\n]*|\n+"
)
_logger = get_logger(__name__)


def _semantic_char_budget(max_steps: int) -> int:
    """Return how many characters may share one decode budget."""
    steps = max(64, min(1200, int(max_steps or 0)))
    return max(
        _SEMANTIC_MIN_BUDGET_CHARS,
        int(steps * _SEMANTIC_BUDGET_RATIO / _SEMANTIC_TOKENS_PER_CHAR),
    )


def _split_text_by_budget(text: str, limit: int) -> tuple[str, ...]:
    """Split text on clause boundaries, never exceeding ``limit`` characters."""
    source = str(text or "")
    if len(source) <= limit:
        return (source,) if source else ()
    pieces: list[str] = []
    for match in _SEMANTIC_CLAUSE_PATTERN.finditer(source):
        piece = match.group(0)
        while len(piece) > limit:
            pieces.append(piece[:limit])
            piece = piece[limit:]
        if piece:
            pieces.append(piece)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > limit:
            chunks.append(current)
            current = ""
        current += piece
    if current:
        chunks.append(current)
    return tuple(chunk for chunk in chunks if chunk.strip())


def _fit_semantic_budget(
    segments: tuple[tuple[str, str], ...],
    max_steps: int,
) -> tuple[tuple[str, str], ...]:
    """Keep every request inside the archive's silent decode cap."""
    limit = _semantic_char_budget(max_steps)
    fitted: list[tuple[str, str]] = []
    for text, language in segments:
        if len(text) <= limit:
            fitted.append((text, language))
            continue
        chunks = _split_text_by_budget(text, limit)
        _logger.info(
            "[Voice] 文本 %d 字超过 %d 步解码预算，已拆分为 %d 段合成",
            len(text), max_steps, len(chunks),
        )
        fitted.extend((chunk, language) for chunk in chunks)
    return tuple(fitted)


def _configure_mixed_language_frontend(module) -> bool:
    """Keep mixed-language phonemes in one semantic inference request."""
    engine_class = getattr(module, "AimisiOnnx", None)
    original_normalize = getattr(module, "normalize_language", None)
    original_phones = getattr(engine_class, "_phones", None)
    module_np = getattr(module, "np", None)
    if (
        engine_class is None
        or not callable(original_normalize)
        or not callable(original_phones)
        or module_np is None
    ):
        return False

    def normalize_mixed_language(value, text):
        normalized = str(value or "auto").strip().lower().replace("_", "-")
        if normalized in {"", "auto", "auto-yue"}:
            return normalize_language("auto", str(text or ""))
        return original_normalize(value, text)

    def mixed_phones(engine, text, language):
        if language != "auto":
            return original_phones(engine, text, language)
        phone_parts = []
        bert_parts = []
        for segment_text, segment_language in _split_auto_language_text(text):
            phones, bert = original_phones(engine, segment_text, segment_language)
            phone_parts.append(phones)
            bert_parts.append(bert)
        if not phone_parts:
            return original_phones(engine, text, "zh")
        return (
            module_np.concatenate(phone_parts, axis=1),
            module_np.concatenate(bert_parts, axis=0),
        )

    module.normalize_language = normalize_mixed_language
    engine_class._phones = mixed_phones
    return True


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
        genie_cleanup = None
        native_sessions: list[NativeGraphSession] = []
        try:
            spec.loader.exec_module(module)
            # Replace the archive's GUI-heavy loader with the isolated,
            # metadata-pinned frontend loader.  Test/future lightweight
            # inference entries that do not expose this hook are left alone.
            if callable(getattr(module, "load_text_frontend", None)):
                genie_state: dict[str, object] = {}

                def load_text_frontend(common_dir):
                    frontend, cleanup = _load_isolated_genie_frontend(Path(common_dir))
                    genie_state["cleanup"] = cleanup
                    return frontend

                module.load_text_frontend = load_text_frontend
            if provider == "cuda":
                # The frontend graphs still run on ONNX Runtime, so CPU support
                # stays a real precondition; the acoustic graphs never touch it.
                providers = module.select_providers("cpu")
                native_sessions = _configure_native_cuda_sessions(module)
            elif provider == "hybrid":
                providers = _configure_hybrid_provider(module)
            else:
                providers = module.select_providers(provider)
            native_mixed_frontend = _configure_mixed_language_frontend(module)
            engine = module.AimisiOnnx(root, providers)
            genie_cleanup = genie_state.get("cleanup") if "genie_state" in locals() else None
        except Exception as exc:
            if callable(locals().get("genie_state", {}).get("cleanup")):
                genie_cleanup = locals()["genie_state"]["cleanup"]
            if callable(genie_cleanup):
                try:
                    genie_cleanup()
                except Exception:
                    pass
            _release_native_sessions(native_sessions)
            sys.modules.pop(module_name, None)
            detail = str(exc).strip() or repr(exc)
            raise OnnxVoiceRuntimeError(f"ONNX 语音模型加载失败：{detail}") from exc

        self.package_root = root
        self.sample_rate = int(validation.manifest.get("sample_rate") or 32000)
        self.provider = provider
        self._native_mixed_frontend = native_mixed_frontend
        self._native_sessions = native_sessions
        self._module_name = module_name
        self._module = module
        self._engine = engine
        self._genie_cleanup = genie_cleanup

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
            ((request.text, request.language),)
            if request.language != "auto" or self._native_mixed_frontend
            else _split_auto_language_text(request.text)
        )
        segments = _fit_semantic_budget(segments, request.max_steps)
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
            samples = np.asarray(audio, dtype=np.float32).reshape(-1)
            expected = len(text) * _SEMANTIC_MIN_SECONDS_PER_CHAR * self.sample_rate
            if samples.size < expected:
                _logger.warning(
                    "[Voice] 合成结果偏短，可能触发了解码上限或提前停止（%d 字 -> %.2f 秒）",
                    len(text), samples.size / max(1, self.sample_rate),
                )
            chunks.append(samples)
            if silence.size and index + 1 < len(segments):
                chunks.append(silence)

        if not chunks:
            raise OnnxVoiceRuntimeError("语音文本不包含可合成片段")
        return np.concatenate(chunks)

    def close(self) -> None:
        self._engine = None
        self._module = None
        _release_native_sessions(getattr(self, "_native_sessions", ()))
        self._native_sessions = []
        cleanup = getattr(self, "_genie_cleanup", None)
        self._genie_cleanup = None
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
        sys.modules.pop(self._module_name, None)
        gc.collect()
