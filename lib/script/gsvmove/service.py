"""Compatibility facade that routes AI voice requests to the ONNX package."""

from __future__ import annotations

import random
import shutil
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue

import config.ollama_config as oc
from config.shared_storage import ensure_shared_config_ready, get_shared_root_dir
from config.user_storage_paths import get_user_cache_dir
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger
from lib.script.gsvmove.hybrid_worker import (
    CudaVoiceWorkerRuntime,
    CpuVoiceWorkerRuntime,
    HybridVoiceWorkerRuntime,
    VoiceWorkerRuntime,
)
from lib.script.gsvmove.package_manager import (
    _read_text_best_effort,
    get_voice_package_status,
    is_valid_legacy_gsvmove_root,
    remove_voice_package as remove_voice_package_files,
    resolve_legacy_gsvmove_root,
)


logger = get_logger(__name__)

_DEFAULT_AUDIO_TYPE = "voice"
_DEFAULT_MEDIA_TYPE = "wav"


def get_gsvmove_launcher_path() -> Path:
    """Compatibility path for the legacy launcher."""
    return get_shared_root_dir() / "start_gsvmove.bat"


def is_gsvmove_launcher_available() -> bool:
    try:
        return get_gsvmove_launcher_path().is_file()
    except Exception:
        return False


def _get_gsv_temperature() -> float:
    raw_value = oc.OLLAMA.get("gsv_temperature", 1.0)
    try:
        temperature = float(raw_value)
    except (TypeError, ValueError):
        temperature = 1.0
    return max(0.01, min(2.0, temperature))


def _get_gsv_speed_factor() -> float:
    raw_value = oc.OLLAMA.get("gsv_speed_factor", 1.0)
    try:
        speed_factor = float(raw_value)
    except (TypeError, ValueError):
        speed_factor = 1.0
    return max(0.5, min(2.0, speed_factor))


def _get_gsv_float(key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(oc.OLLAMA.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _get_gsv_int(key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(oc.OLLAMA.get(key, default))
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(maximum, value))


def _get_gsv_inference_defaults() -> dict:
    split_method = str(oc.OLLAMA.get("gsv_text_split_method", "cut5") or "cut5").strip().lower()
    if split_method not in {"cut0", "cut1", "cut2", "cut3", "cut4", "cut5"}:
        split_method = "cut5"
    return {
        "temperature": _get_gsv_temperature(),
        "top_k": _get_gsv_int("gsv_top_k", 15, 1, 1025),
        "top_p": _get_gsv_float("gsv_top_p", 1.0, 0.01, 1.0),
        "repetition_penalty": _get_gsv_float("gsv_repetition_penalty", 1.35, 0.1, 2.0),
        "speed_factor": _get_gsv_speed_factor(),
        "text_split_method": split_method,
        "fragment_interval": _get_gsv_float("gsv_fragment_interval", 0.3, 0.0, 5.0),
        "seed": _get_gsv_int("gsv_seed", -1, -1, 2**32 - 1),
        "max_steps": _get_gsv_int("gsv_max_steps", 500, 64, 1200),
    }


def _get_gsv_cache_max_files() -> int:
    raw_value = oc.OLLAMA.get("gsv_cache_max_files", 20)
    try:
        max_files = int(float(raw_value))
    except (TypeError, ValueError):
        max_files = 20
    return max(1, min(128, max_files))


def _is_gsv_auto_start_enabled() -> bool:
    raw_value = oc.OLLAMA.get("gsv_auto_start", True)
    if isinstance(raw_value, str):
        return raw_value.strip().lower() not in {"", "0", "false", "off", "no"}
    return bool(raw_value)


def _is_gsv_nvidia_cuda_acceleration_enabled() -> bool:
    raw_value = oc.OLLAMA.get("gsv_nvidia_cuda_acceleration", False)
    if isinstance(raw_value, str):
        return raw_value.strip().lower() not in {"", "0", "false", "off", "no"}
    return bool(raw_value)


def _is_gsv_gpu_hybrid_enabled() -> bool:
    """Compatibility switch for the existing DirectML fallback."""
    raw_value = oc.OLLAMA.get("gsv_gpu_hybrid", False)
    if isinstance(raw_value, str):
        return raw_value.strip().lower() not in {"", "0", "false", "off", "no"}
    return bool(raw_value)


def is_voice_package_available() -> bool:
    return not get_voice_package_status().install_required


class GsvmoveService:
    """Keep the historical service API while using a local ONNX engine."""

    def __init__(self) -> None:
        self._ec = get_event_center()
        self._infer_lock = threading.RLock()
        self._engine_lock = threading.RLock()
        self._engine: VoiceWorkerRuntime | None = None
        self._engine_package_root: Path | None = None
        self._engine_backend: str | None = None
        self._engine_requested_backend: str | None = None
        self._request_queue: Queue[dict | None] = Queue()
        self._worker_stop = threading.Event()
        self._warmup_lock = threading.Lock()
        self._warmup_done = False
        self._prestart_lock = threading.Lock()
        self._prestart_started = False
        self._project_root = Path(__file__).resolve().parents[3]
        self._launcher_path = get_gsvmove_launcher_path()
        self._output_dir = get_user_cache_dir("gsvmove", "output")
        self._saved_audio_root = get_user_cache_dir("gsvmove", "voice")
        self._saved_audio_lock = threading.Lock()
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._saved_audio_root.mkdir(parents=True, exist_ok=True)
        self.cleanup_saved_audio_cache()

        self._ec.subscribe(EventType.APP_MAIN, self._on_app_main)
        self._ec.subscribe(EventType.AI_VOICE_REQUEST, self._on_ai_voice_request)
        self._ec.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        self._worker = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="gsvmove-worker",
        )
        self._worker.start()
        logger.info("[GsvmoveService] ONNX 语音桥接已初始化")

    def _is_valid_gsvmove_root(self, root: Path | None) -> bool:
        return is_valid_legacy_gsvmove_root(root)

    def _resolve_gsvmove_root(self) -> tuple[Path | None, Path | None]:
        return resolve_legacy_gsvmove_root(self._launcher_path)

    def resolve_install_root(self) -> Path | None:
        root, _ = self._resolve_gsvmove_root()
        return root if self._is_valid_gsvmove_root(root) else None

    def auto_start_enabled(self) -> bool:
        return _is_gsv_auto_start_enabled()

    def _on_app_main(self, _event: Event) -> None:
        self.kickoff_prestart()

    def _on_config_updated(self, event: Event) -> None:
        data = event.data or {}
        if data.get("source") != "ai":
            return
        values = data.get("values")
        if not isinstance(values, dict) or not {
            "gsv_nvidia_cuda_acceleration",
            "gsv_gpu_hybrid",
        }.intersection(values):
            return
        if bool(values.get("gsv_nvidia_cuda_acceleration")):
            desired_backend = "cuda"
        elif bool(values.get("gsv_gpu_hybrid")):
            desired_backend = "hybrid"
        else:
            desired_backend = "cpu"
        with self._engine_lock:
            if self._engine is None or self._engine_requested_backend == desired_backend:
                return
        get_compute_hub().submit_latest(
            "gsvmove_backend_switch",
            self._switch_backend_after_config,
            executor="vector",
        )

    def _switch_backend_after_config(self) -> None:
        with self._infer_lock:
            with self._engine_lock:
                self._close_engine_locked()
            self._warmup_done = False
            with self._prestart_lock:
                self._prestart_started = False
        if self.auto_start_enabled():
            self.kickoff_prestart()

    def kickoff_prestart(self) -> None:
        if not self.auto_start_enabled():
            return
        with self._prestart_lock:
            if self._prestart_started:
                return
            self._prestart_started = True
        try:
            future = get_compute_hub().submit_latest(
                "gsvmove_prestart",
                self._prestart_worker,
                executor="vector",
            )
            if future is None:
                logger.debug("[GsvmoveService] ONNX 预加载任务仍在运行")
        except Exception:
            with self._prestart_lock:
                self._prestart_started = False
            raise

    def _prestart_worker(self) -> None:
        try:
            if not self.auto_start_enabled():
                return
            with self._infer_lock:
                if self._ensure_runtime_ready():
                    self._warmup_service_once()
        finally:
            if not self._warmup_done:
                with self._prestart_lock:
                    self._prestart_started = False

    def _ensure_runtime_ready(self) -> bool:
        status = get_voice_package_status()
        package_root = status.package_root
        if status.install_required or package_root is None:
            logger.warning("[GsvmoveService] ONNX 语音包不可用: %s", status.reason)
            return False

        if _is_gsv_nvidia_cuda_acceleration_enabled():
            desired_backend = "cuda"
        elif _is_gsv_gpu_hybrid_enabled():
            desired_backend = "hybrid"
        else:
            desired_backend = "cpu"
        with self._engine_lock:
            if (
                self._engine is not None
                and self._engine_package_root == package_root
                and self._engine_requested_backend == desired_backend
            ):
                return True
            self._close_engine_locked()
            self._engine_requested_backend = desired_backend
            try:
                started_at = time.monotonic()
                if desired_backend == "cuda":
                    self._engine = CudaVoiceWorkerRuntime(package_root, self._output_dir)
                    self._engine_backend = "cuda"
                elif desired_backend == "hybrid":
                    self._engine = HybridVoiceWorkerRuntime(package_root, self._output_dir)
                    self._engine_backend = "hybrid"
                else:
                    self._engine = CpuVoiceWorkerRuntime(package_root, self._output_dir)
                    self._engine_backend = "cpu"
                self._engine_package_root = package_root
                logger.info(
                    "[GsvmoveService] ONNX 语音模型已就绪 backend=%s dt=%.1fs",
                    self._engine_backend,
                    time.monotonic() - started_at,
                )
                return True
            except Exception as exc:
                if desired_backend == "hybrid":
                    logger.warning(
                        "[GsvmoveService] DirectML 混合推理加载失败，回退 CPU: %s",
                        exc,
                    )
                    return self._activate_cpu_fallback_locked(package_root)
                if desired_backend != "cuda":
                    self._engine = None
                    self._engine_package_root = None
                    self._engine_backend = None
                    logger.error("[GsvmoveService] ONNX 语音模型加载失败: %s", exc)
                    return False
                logger.warning(
                    "[GsvmoveService] CUDA 推理加载失败，尝试 DirectML 后备: %s",
                    exc,
                )
                return self._activate_directml_fallback_locked(package_root)

    def _activate_directml_fallback_locked(self, package_root: Path) -> bool:
        self._close_engine_locked(reset_requested_backend=False)
        try:
            self._engine = HybridVoiceWorkerRuntime(package_root, self._output_dir)
            self._engine_package_root = package_root
            self._engine_backend = "directml-fallback"
            self._engine_requested_backend = "cuda"
            logger.info("[GsvmoveService] CUDA 不可用，已切换 DirectML 后备")
            return True
        except Exception as exc:
            logger.warning("[GsvmoveService] DirectML 后备不可用，回退 CPU: %s", exc)
            return self._activate_cpu_fallback_locked(
                package_root,
                requested_backend="cuda",
            )

    def _activate_cpu_fallback_locked(
        self,
        package_root: Path,
        *,
        requested_backend: str = "hybrid",
    ) -> bool:
        self._close_engine_locked(reset_requested_backend=False)
        try:
            self._engine = CpuVoiceWorkerRuntime(package_root, self._output_dir)
            self._engine_package_root = package_root
            self._engine_backend = "cpu-fallback"
            self._engine_requested_backend = requested_backend
            return True
        except Exception as exc:
            self._engine = None
            self._engine_package_root = None
            self._engine_backend = None
            logger.error("[GsvmoveService] CPU 回退模型加载失败: %s", exc)
            return False

    def prepare_voice_package_install(self) -> None:
        """Release active model files before a package is atomically replaced."""
        with self._infer_lock:
            with self._engine_lock:
                self._close_engine_locked()
            self._warmup_done = False
            with self._prestart_lock:
                self._prestart_started = False

    def reload_voice_package(self) -> None:
        self.prepare_voice_package_install()
        self.kickoff_prestart()

    def remove_voice_package(self, package_root: Path) -> Path:
        """Release the active engine and remove a managed package without an inference race."""
        with self._infer_lock:
            self.prepare_voice_package_install()
            return remove_voice_package_files(package_root)

    def shutdown_service_process(self) -> bool:
        self.prepare_voice_package_install()
        return True

    def _close_engine_locked(self, *, reset_requested_backend: bool = True) -> None:
        engine = self._engine
        self._engine = None
        self._engine_package_root = None
        self._engine_backend = None
        if reset_requested_backend:
            self._engine_requested_backend = None
        if engine is not None:
            try:
                engine.close()
            except Exception as exc:
                logger.debug("[GsvmoveService] 释放 ONNX 运行时失败: %s", exc)

    def get_saved_audio_cache_root(self) -> Path:
        self._saved_audio_root.mkdir(parents=True, exist_ok=True)
        return self._saved_audio_root

    def cleanup_saved_audio_cache(self) -> None:
        with self._saved_audio_lock:
            self._cleanup_saved_audio_cache_locked()

    def _cleanup_saved_audio_cache_locked(self) -> None:
        cache_root = self.get_saved_audio_cache_root()
        entries: list[tuple[float, Path]] = []
        for child in cache_root.iterdir():
            try:
                mtime = child.stat().st_mtime
            except OSError:
                mtime = 0.0
            entries.append((mtime, child))
        max_files = _get_gsv_cache_max_files()
        if len(entries) <= max_files:
            return
        entries.sort(key=lambda item: item[0])
        for _, old_path in entries[:-max_files]:
            try:
                if old_path.is_dir():
                    shutil.rmtree(old_path)
                else:
                    old_path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning("[GsvmoveService] 清理旧语音缓存失败 %s: %s", old_path, exc)

    def _create_saved_audio_path(self, suffix: str) -> Path:
        cache_root = self.get_saved_audio_cache_root()
        while True:
            millis = int((time.time() % 1) * 1000)
            name = (
                f"voice_{time.strftime('%Y%m%d_%H%M%S')}_{millis:03d}_"
                f"{uuid.uuid4().hex[:8]}{suffix}"
            )
            candidate = cache_root / name
            if not candidate.exists():
                return candidate

    def _persist_generated_audio(self, temp_path: Path) -> Path:
        with self._saved_audio_lock:
            target = self._create_saved_audio_path(temp_path.suffix or ".wav")
            temp_path.replace(target)
            self._cleanup_saved_audio_cache_locked()
            return target

    @staticmethod
    def _load_launch_hello_lines() -> list[str]:
        hello_path = Path(__file__).resolve().parents[3] / "resc" / "launch_hello.txt"
        try:
            lines = [line.strip() for line in hello_path.read_text(encoding="utf-8").splitlines()]
        except Exception as exc:
            logger.warning("[GsvmoveService] 读取预热文案失败: %s", exc)
            return []
        return [line for line in lines if line]

    def _warmup_service_once(self) -> None:
        with self._warmup_lock:
            if self._warmup_done:
                return
            lines = self._load_launch_hello_lines()
            if not lines:
                return
            warmup_text = random.choice(lines)
            try:
                with self._infer_lock:
                    warmup_file = self._synthesize_to_file({
                        "text": warmup_text,
                        "interruptible": True,
                        "save_audio_cache": False,
                    })
                if warmup_file is None:
                    return
                warmup_file.unlink(missing_ok=True)
                self._warmup_done = True
                logger.info("[GsvmoveService] 已完成 ONNX 语音预热: %s", warmup_text[:40])
            except Exception as exc:
                logger.warning("[GsvmoveService] ONNX 语音预热失败: %s", exc)

    def _on_ai_voice_request(self, event: Event) -> None:
        data = event.data or {}
        if not str(data.get("text") or "").strip() or event.handled:
            return
        if not self._accepts_companion_generation(data):
            event.mark_handled()
            return
        if not self.auto_start_enabled():
            logger.info("[GsvmoveService] 语音模块已关闭，忽略文本语音申请")
            event.mark_handled()
            return
        self._request_queue.put(dict(data))
        event.mark_handled()

    def _worker_loop(self) -> None:
        while not self._worker_stop.is_set():
            try:
                data = self._request_queue.get(timeout=0.5)
            except Empty:
                continue
            if data is None:
                break
            try:
                self._process_ai_voice_request(data)
            except Exception as exc:
                logger.error("[GsvmoveService] 后台处理 AI 语音申请失败: %s", exc)
            finally:
                self._request_queue.task_done()

    def _process_ai_voice_request(self, data: dict) -> None:
        if not self.auto_start_enabled() or not self._accepts_companion_generation(data):
            return
        with self._infer_lock:
            audio_file = self._synthesize_to_file(data)
        if audio_file is None:
            return
        if not self._accepts_companion_generation(data):
            audio_file.unlink(missing_ok=True)
            return
        self._ec.publish(Event(EventType.SOUND_REQUEST, {
            "audio_type": _DEFAULT_AUDIO_TYPE,
            "source": str(audio_file),
            "volume_gain": 1.0,
            "interruptible": bool(data.get("interruptible", True)),
        }))

    @staticmethod
    def _accepts_companion_generation(data: dict) -> bool:
        raw_generation = data.get("mode_generation")
        if raw_generation is None:
            return True
        try:
            generation = int(raw_generation)
        except (TypeError, ValueError):
            return False
        try:
            from lib.script.office.mode import get_interaction_mode_service

            return get_interaction_mode_service().accepts_companion_generation(generation)
        except Exception:
            return False

    def _synthesize_to_file(self, data: dict) -> Path | None:
        if not self._ensure_runtime_ready():
            return None
        payload = dict(data)
        for key, value in _get_gsv_inference_defaults().items():
            payload.setdefault(key, value)
        temp_path = self._output_dir / f"onnx_{uuid.uuid4().hex}.{_DEFAULT_MEDIA_TYPE}"
        try:
            with self._engine_lock:
                engine = self._engine
                if engine is None:
                    return None
                try:
                    engine.synthesize_to_file(payload, temp_path)
                except Exception as exc:
                    if self._engine_backend not in {
                        "hybrid",
                        "cuda",
                        "directml-fallback",
                    } or self._engine_package_root is None:
                        raise
                    package_root = self._engine_package_root
                    temp_path.unlink(missing_ok=True)
                    logger.warning(
                        "[GsvmoveService] GPU 推理失败，当前请求回退 CPU: %s",
                        exc,
                    )
                    requested_backend = self._engine_requested_backend or "hybrid"
                    if requested_backend == "hybrid":
                        fallback_ready = self._activate_cpu_fallback_locked(package_root)
                    else:
                        fallback_ready = self._activate_cpu_fallback_locked(
                            package_root,
                            requested_backend=requested_backend,
                        )
                    if not fallback_ready:
                        raise
                    fallback = self._engine
                    if fallback is None:
                        raise
                    fallback.synthesize_to_file(payload, temp_path)
            output_path = temp_path
            if bool(data.get("save_audio_cache", True)):
                try:
                    output_path = self._persist_generated_audio(temp_path)
                except Exception as exc:
                    logger.warning("[GsvmoveService] 归档语音缓存失败: %s", exc)
            logger.info("[GsvmoveService] 已生成 ONNX 语音文件: %s", output_path)
            return output_path
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            logger.error("[GsvmoveService] ONNX 语音推理失败: %s", exc)
            return None

    def cleanup(self) -> None:
        try:
            self._ec.unsubscribe(EventType.APP_MAIN, self._on_app_main)
        except Exception:
            pass
        try:
            self._ec.unsubscribe(EventType.AI_VOICE_REQUEST, self._on_ai_voice_request)
        except Exception:
            pass
        try:
            self._ec.unsubscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        except Exception:
            pass
        self._worker_stop.set()
        self._request_queue.put(None)
        if self._worker is not threading.current_thread():
            self._worker.join(timeout=2.0)
        with self._infer_lock:
            with self._engine_lock:
                self._close_engine_locked()


_instance: GsvmoveService | None = None


def get_gsvmove_service() -> GsvmoveService:
    global _instance
    if _instance is None:
        ensure_shared_config_ready()
        _instance = GsvmoveService()
    return _instance


def cleanup_gsvmove_service() -> None:
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
