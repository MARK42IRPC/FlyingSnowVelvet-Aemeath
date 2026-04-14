"""GSVmove TTS service bridge.

职责：
- APP_PRE_START 阶段后台隐藏拉起 `C:\\AemeathDeskPet\\start_gsvmove.bat`
- 监听 `AI_VOICE_REQUEST` 中的文本 TTS 请求
- 调用本地 GSVmove HTTP API 生成音频，并回灌为 `SOUND_REQUEST`
"""

import locale
import shutil
import subprocess
import threading
import time
import uuid
import random
import os
import re
from queue import Empty, Queue
from pathlib import Path

import requests

try:
    from packaging.requirements import Requirement
except Exception:
    Requirement = None

import config.ollama_config as oc
from config.shared_storage import (
    ensure_shared_config_ready,
    get_shared_config_path,
    get_shared_root_dir,
)
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 9880
_DEFAULT_TIMEOUT = (3.0, 120.0)
_DEFAULT_AUDIO_CLASS = "voice"
_DEFAULT_MEDIA_TYPE = "wav"
_STARTUP_WAIT_SECS = 45.0
_HEALTH_POLL_INTERVAL = 0.5
_SHORT_TEXT_SPLIT_METHOD = "cut0"
_LAUNCHER_LOG_TAIL_LINES = 12
_CMUDICT_MISSING_PATTERN = re.compile(r"Resource 'cmudict' not found", re.I)
_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z]+(?:[A-Za-z0-9_./:+-]*[A-Za-z0-9])?")
_BATCH_SET_PATTERN = re.compile(r'(?im)^\s*set\s+"?(?P<name>[A-Za-z_]\w*)=(?P<value>[^\r\n"]*)"?\s*$')
_BATCH_FIND_ROOT_PATTERN = re.compile(r'(?im)^\s*call\s+:find_root\s+"(?P<path>[^"\r\n]+)"')
_BATCH_EXPAND_ROUNDS = 8




def _get_gsv_temperature() -> float:
    raw_value = oc.OLLAMA.get("gsv_temperature", 1.35)
    try:
        temperature = float(raw_value)
    except (TypeError, ValueError):
        temperature = 1.35
    return max(0.0, min(2.0, temperature))


def _get_gsv_speed_factor() -> float:
    raw_value = oc.OLLAMA.get("gsv_speed_factor", 1.0)
    try:
        speed_factor = float(raw_value)
    except (TypeError, ValueError):
        speed_factor = 1.0
    return max(0.5, min(2.0, speed_factor))


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
        normalized = raw_value.strip().lower()
        return normalized not in {"", "0", "false", "off", "no"}
    return bool(raw_value)


def _extract_response_detail(resp: requests.Response) -> str:
    try:
        return str(resp.json())
    except Exception:
        return (resp.text or "").strip()


def _build_ascii_safe_tts_text(text: str) -> str:
    cleaned = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return ""

    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"`[^`]+`", "", cleaned)
    cleaned = _ASCII_TOKEN_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s*([，。！？、,.!?；;：:])\s*", r"\1", cleaned)
    cleaned = re.sub(r"([，。！？、,.!?；;：:]){2,}", lambda m: m.group(0)[0], cleaned)
    return cleaned.strip(" \t\n\r，。！？、,.!?；;：:")


def _read_text_best_effort(path: Path) -> str:
    data = path.read_bytes()
    encodings: list[str] = []
    preferred = locale.getpreferredencoding(False)
    for encoding in ('utf-8', 'utf-8-sig', preferred, 'mbcs', 'gbk'):
        if encoding and encoding not in encodings:
            encodings.append(encoding)
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode(errors='ignore')


def _write_text_best_effort(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encodings: list[str] = []
    preferred = locale.getpreferredencoding(False)
    for encoding in (preferred, 'mbcs', 'utf-8'):
        if encoding and encoding not in encodings:
            encodings.append(encoding)
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            path.write_text(text, encoding=encoding)
            return
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error


def _expand_batch_value(value: str, variables: dict[str, str], script_dir: Path) -> str:
    expanded = str(value or '').replace('%~dp0', f'{script_dir}{os.sep}')
    for _ in range(_BATCH_EXPAND_ROUNDS):
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1).strip().upper()
            if key in variables:
                return variables[key]
            env_value = os.environ.get(key)
            if env_value is not None:
                return env_value
            return match.group(0)

        updated = re.sub(r'%([^%]+)%', _replace, expanded)
        if updated == expanded:
            break
        expanded = updated
    return expanded.strip().strip('"')


def _parse_batch_variables(text: str, script_dir: Path) -> dict[str, str]:
    variables = {'SCRIPT_DIR': f'{script_dir}{os.sep}'}
    for match in _BATCH_SET_PATTERN.finditer(text):
        name = match.group('name').upper()
        value = _expand_batch_value(match.group('value'), variables, script_dir)
        variables[name] = value
    return variables




class GsvmoveService:
    """GSVmove 文本转语音桥接服务。"""

    def __init__(self):
        self._ec = get_event_center()
        self._session = requests.Session()
        self._proc_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._request_queue: Queue[dict | None] = Queue()
        self._worker_stop = threading.Event()
        self._process: subprocess.Popen | None = None
        self._started_by_app = False
        self._prestart_lock = threading.Lock()
        self._prestart_started = False
        self._warmup_lock = threading.Lock()
        self._warmup_done = False
        self._host = _DEFAULT_HOST
        self._port = _DEFAULT_PORT
        self._project_root = Path(__file__).resolve().parents[3]
        self._root_dir = get_shared_root_dir()
        self._launcher_path = self._root_dir / "start_gsvmove.bat"
        self._launcher_log_path = get_shared_config_path("gsvmove", "launcher.log")
        self._output_dir = get_shared_config_path("gsvmove", "cache")
        self._saved_audio_root = self._project_root / "resc" / "user" / "temp" / "gsv_voice"
        self._saved_audio_lock = threading.Lock()
        self._launcher_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._saved_audio_root.mkdir(parents=True, exist_ok=True)
        self.cleanup_saved_audio_cache()

        self._ec.subscribe(EventType.APP_PRE_START, self._on_app_pre_start)
        self._ec.subscribe(EventType.AI_VOICE_REQUEST, self._on_ai_voice_request)
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="gsvmove-worker")
        self._worker.start()
        logger.info("[GsvmoveService] 已初始化")

    @property
    def _base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def _is_valid_gsvmove_root(self, root: Path | None) -> bool:
        if root is None:
            return False
        candidate = Path(root)
        return (
            candidate.exists()
            and (candidate / 'start.bat').exists()
            and (candidate / 'api.py').exists()
            and (candidate / 'configs' / 'tts_infer.yaml').exists()
            and (candidate / '.venv' / 'Scripts' / 'python.exe').exists()
        )

    def _resolve_launcher_root_file(self, launcher_text: str) -> Path:
        variables = _parse_batch_variables(launcher_text, self._launcher_path.parent)
        root_file = variables.get('ROOT_FILE', '')
        if root_file:
            return Path(root_file)
        return self._launcher_path.parent / 'config' / 'gsvmove_root.txt'

    def _resolve_search_bases_from_launcher(self, launcher_text: str) -> list[Path]:
        variables = _parse_batch_variables(launcher_text, self._launcher_path.parent)
        bases: list[Path] = []
        seen: set[str] = set()
        for match in _BATCH_FIND_ROOT_PATTERN.finditer(launcher_text):
            raw_path = _expand_batch_value(match.group('path'), variables, self._launcher_path.parent)
            if not raw_path:
                continue
            candidate = Path(raw_path)
            key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            bases.append(candidate)
        return bases

    def _find_gsvmove_root_in_base(self, base: Path) -> Path | None:
        if not base.exists():
            return None
        skip_dirs = {'.git', '.hg', '.svn', '.venv', '__pycache__', 'node_modules'}
        for current_root, dirnames, filenames in os.walk(base):
            dirnames[:] = [item for item in dirnames if item not in skip_dirs]
            if 'start.bat' not in filenames:
                continue
            candidate = Path(current_root)
            if self._is_valid_gsvmove_root(candidate):
                return candidate
        return None

    def _resolve_gsvmove_root(self) -> tuple[Path | None, Path | None]:
        if not self._launcher_path.exists():
            return None, None
        try:
            launcher_text = _read_text_best_effort(self._launcher_path)
        except Exception as e:
            logger.warning('[GsvmoveService] 读取启动脚本失败: %s', e)
            return None, None

        root_file = self._resolve_launcher_root_file(launcher_text)
        if root_file.exists():
            try:
                configured_root_text = _read_text_best_effort(root_file).strip().strip('"')
                configured_root = Path(configured_root_text) if configured_root_text else None
            except Exception:
                configured_root = None
            if self._is_valid_gsvmove_root(configured_root):
                return configured_root, root_file

        for base in self._resolve_search_bases_from_launcher(launcher_text):
            candidate = self._find_gsvmove_root_in_base(base)
            if candidate is None:
                continue
            try:
                _write_text_best_effort(root_file, str(candidate))
            except Exception as e:
                logger.debug('[GsvmoveService] 回写 gsvmove_root.txt 失败: %s', e)
            return candidate, root_file
        return None, root_file


    def resolve_install_root(self) -> Path | None:
        root, _ = self._resolve_gsvmove_root()
        if root is None or not self._is_valid_gsvmove_root(root):
            return None
        return root

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
                logger.info("[GsvmoveService] 已清理旧语音缓存: %s", old_path)
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.warning("[GsvmoveService] 清理旧语音缓存失败 %s: %s", old_path, e)

    def _create_saved_audio_path(self, suffix: str) -> Path:
        cache_root = self.get_saved_audio_cache_root()
        while True:
            millis = int((time.time() % 1) * 1000)
            file_name = f"voice_{time.strftime('%Y%m%d_%H%M%S')}_{millis:03d}_{uuid.uuid4().hex[:8]}{suffix}"
            candidate = cache_root / file_name
            if candidate.exists():
                continue
            return candidate

    def _persist_generated_audio(self, temp_path: Path) -> Path:
        suffix = temp_path.suffix or ".wav"
        with self._saved_audio_lock:
            target_path = self._create_saved_audio_path(suffix)
            temp_path.replace(target_path)
            self._cleanup_saved_audio_cache_locked()
            return target_path

    def _on_app_pre_start(self, event: Event):
        del event
        if not self.auto_start_enabled():
            return
        self.kickoff_prestart()

    def auto_start_enabled(self) -> bool:
        return _is_gsv_auto_start_enabled()

    def kickoff_prestart(self) -> None:
        if not self.auto_start_enabled():
            return
        with self._prestart_lock:
            if self._prestart_started:
                return
            self._prestart_started = True
        threading.Thread(target=self._prestart_worker, daemon=True, name="gsvmove-prestart").start()

    def _prestart_worker(self) -> None:
        if not self._ensure_service_ready():
            return
        self._warmup_service_once()

    @staticmethod
    def _load_launch_hello_lines() -> list[str]:
        hello_path = Path(__file__).resolve().parents[3] / 'resc' / 'launch_hello.txt'
        try:
            lines = [line.strip() for line in hello_path.read_text(encoding='utf-8').splitlines()]
        except Exception as e:
            logger.warning('[GsvmoveService] 读取预热文案失败: %s', e)
            return []
        return [line for line in lines if line]

    def _warmup_service_once(self) -> None:
        with self._warmup_lock:
            if self._warmup_done:
                return
            lines = self._load_launch_hello_lines()
            if not lines:
                logger.warning('[GsvmoveService] 预热文案为空，跳过 GSV 预热')
                return
            warmup_text = random.choice(lines)
            try:
                with self._infer_lock:
                    warmup_file = self._synthesize_to_file({
                        'text': warmup_text,
                        'interruptible': True,
                        'save_audio_cache': False,
                    })
                if warmup_file is None:
                    logger.warning('[GsvmoveService] GSV 预热失败：未生成音频')
                    return
                try:
                    warmup_file.unlink(missing_ok=True)
                except Exception:
                    pass
                self._warmup_done = True
                logger.info('[GsvmoveService] 已完成 GSV 预热: %s', warmup_text[:40])
            except Exception as e:
                logger.warning('[GsvmoveService] GSV 预热失败: %s', e)

    def _on_ai_voice_request(self, event: Event):
        data = event.data or {}
        text = str(data.get("text") or "").strip()
        if not text:
            return

        if event.handled:
            return

        if not self.auto_start_enabled():
            logger.info("[GsvmoveService] GSV 语音模块已关闭，忽略文本语音申请")
            event.mark_handled()
            return

        self._request_queue.put(dict(data))
        event.mark_handled()

    def _worker_loop(self):
        while not self._worker_stop.is_set():
            try:
                data = self._request_queue.get(timeout=0.5)
            except Empty:
                continue
            if data is None:
                break
            try:
                self._process_ai_voice_request(data)
            except Exception as e:
                logger.error("[GsvmoveService] 后台处理 AI 语音申请失败: %s", e)
            finally:
                self._request_queue.task_done()

    def _process_ai_voice_request(self, data: dict):
        if not self.auto_start_enabled():
            logger.info("[GsvmoveService] GSV 语音模块已关闭，跳过文本语音处理")
            return

        if not self._ensure_service_ready():
            logger.warning("[GsvmoveService] GSVmove 服务未就绪，忽略文本语音申请")
            return

        with self._infer_lock:
            audio_file = self._synthesize_to_file(data)
        if audio_file is None:
            return

        self._ec.publish(Event(EventType.SOUND_REQUEST, {
            "audio_class": _DEFAULT_AUDIO_CLASS,
            "file_path": str(audio_file),
            # 这里固定基准音量为 1.0，实际语音响度由 VoiceCore 内部统一套用 VOICE.voice_volume。
            "volume": 1.0,
            "interruptible": bool(data.get("interruptible", True)),
        }))

    def _health_check(self, timeout: float = 2.0) -> bool:
        try:
            resp = self._session.get(f"{self._base_url}/health", timeout=timeout)
            if not resp.ok:
                return False
            payload = resp.json()
            return str(payload.get("status") or "").lower() == "ok"
        except Exception:
            return False

    def _ensure_service_ready(self) -> bool:
        if self._health_check():
            return True

        self._start_service_process()
        deadline = time.monotonic() + _STARTUP_WAIT_SECS
        while time.monotonic() < deadline:
            if self._health_check():
                logger.info("[GsvmoveService] GSVmove 服务已就绪")
                return True
            with self._proc_lock:
                proc = self._process
            if proc is not None:
                exit_code = proc.poll()
                if exit_code is not None:
                    with self._proc_lock:
                        if self._process is proc:
                            self._process = None
                    log_tail = self._read_launcher_log_tail()
                    if log_tail:
                        logger.error(
                            "[GsvmoveService] GSVmove 启动器提前退出 exit_code=%s，最近输出:\n%s",
                            exit_code,
                            log_tail,
                        )
                    else:
                        logger.error("[GsvmoveService] GSVmove 启动器提前退出 exit_code=%s", exit_code)
                    return False
            time.sleep(_HEALTH_POLL_INTERVAL)
        logger.warning("[GsvmoveService] GSVmove 服务启动超时")
        return False

    def _start_service_process(self):
        with self._proc_lock:
            if self._process is not None and self._process.poll() is None:
                return
            if not self._launcher_path.exists():
                logger.warning("[GsvmoveService] 未找到启动脚本: %s", self._launcher_path)
                return
            launcher_log = None
            try:
                launcher_log = self._launcher_log_path.open("ab")
                launcher_log.write(
                    f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start_gsvmove =====\n".encode("utf-8")
                )
                launcher_log.flush()
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self._process = subprocess.Popen(
                    ["cmd", "/c", str(self._launcher_path)],
                    cwd=str(self._root_dir),
                    stdin=subprocess.DEVNULL,
                    stdout=launcher_log,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
                self._started_by_app = True
                logger.info("[GsvmoveService] 已在后台启动 GSVmove")
            except Exception as e:
                self._process = None
                self._started_by_app = False
                logger.error("[GsvmoveService] 启动 GSVmove 失败: %s", e)
            finally:
                if launcher_log is not None:
                    try:
                        launcher_log.close()
                    except Exception:
                        pass

    def _read_launcher_log_tail(self, max_lines: int = _LAUNCHER_LOG_TAIL_LINES) -> str:
        try:
            if not self._launcher_log_path.exists():
                return ""
            text = self._launcher_log_path.read_bytes().decode(errors="ignore")
            lines = [line.rstrip() for line in text.splitlines() if line.strip()]
            if not lines:
                return ""
            return "\n".join(lines[-max_lines:])
        except Exception as e:
            logger.debug("[GsvmoveService] 读取启动器日志失败: %s", e)
            return ""

    def _synthesize_to_file(self, data: dict) -> Path | None:
        text = str(data.get("text") or "").strip()
        payload = {
            "text": text,
            "text_lang": str(data.get("text_lang") or "zh").strip() or "zh",
            "prompt_lang": str(data.get("prompt_lang") or "zh").strip() or "zh",
            "prompt_text": data.get("prompt_text"),
            "ref_audio_path": data.get("ref_audio_path"),
            "aux_ref_audio_paths": data.get("aux_ref_audio_paths"),
            "top_k": int(data.get("top_k", 15) or 15),
            "top_p": float(data.get("top_p", 1.0) or 1.0),
            "temperature": max(0.0, min(2.0, float(data.get("temperature", _get_gsv_temperature()) or _get_gsv_temperature()))),
            # Short dialogue should not be split by punctuation, otherwise each chunk may reintroduce prompt leakage.
            "text_split_method": str(data.get("text_split_method") or _SHORT_TEXT_SPLIT_METHOD),
            "batch_size": int(data.get("batch_size", 1) or 1),
            "batch_threshold": float(data.get("batch_threshold", 0.75) or 0.75),
            "split_bucket": bool(data.get("split_bucket", False)),
            "speed_factor": max(0.5, min(2.0, float(data.get("speed_factor", _get_gsv_speed_factor()) or _get_gsv_speed_factor()))),
            "fragment_interval": float(data.get("fragment_interval", 0.0) or 0.0),
            "seed": int(data.get("seed", -1) or -1),
            "media_type": str(data.get("media_type") or _DEFAULT_MEDIA_TYPE).strip() or _DEFAULT_MEDIA_TYPE,
            "streaming_mode": False,
            "parallel_infer": bool(data.get("parallel_infer", False)),
            "repetition_penalty": float(data.get("repetition_penalty", 1.35) or 1.35),
            "sample_steps": int(data.get("sample_steps", 32) or 32),
            "super_sampling": bool(data.get("super_sampling", False)),
            "overlap_length": int(data.get("overlap_length", 2) or 2),
            "min_chunk_length": int(data.get("min_chunk_length", 16) or 16),
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        try:
            resp = self._session.post(f"{self._base_url}/tts", json=payload, timeout=_DEFAULT_TIMEOUT)
            if not resp.ok:
                detail = _extract_response_detail(resp)
                if _CMUDICT_MISSING_PATTERN.search(detail):
                    fallback_text = _build_ascii_safe_tts_text(text)
                    if fallback_text and fallback_text != text:
                        fallback_payload = dict(payload)
                        fallback_payload["text"] = fallback_text
                        logger.warning(
                            "[GsvmoveService] 检测到 cmudict 缺失，已改用清洗后的文本重试 TTS: %s -> %s",
                            text[:80],
                            fallback_text[:80],
                        )
                        resp = self._session.post(f"{self._base_url}/tts", json=fallback_payload, timeout=_DEFAULT_TIMEOUT)
                        if resp.ok:
                            payload = fallback_payload
                        else:
                            detail = _extract_response_detail(resp)
                if not resp.ok:
                    logger.warning("[GsvmoveService] TTS 请求失败 status=%s detail=%s", resp.status_code, detail[:300])
                    return None
            media_type = str(payload.get("media_type") or _DEFAULT_MEDIA_TYPE).lower()
            suffix = f".{media_type if media_type in ('wav', 'ogg', 'aac', 'raw') else 'wav'}"
            temp_path = self._output_dir / f"gsv_{uuid.uuid4().hex}{suffix}"
            temp_path.write_bytes(resp.content)
            output_path = temp_path
            if bool(data.get("save_audio_cache", True)):
                try:
                    output_path = self._persist_generated_audio(temp_path)
                except Exception as e:
                    logger.warning("[GsvmoveService] 归档语音缓存失败，继续使用临时文件: %s", e)
                    output_path = temp_path
            logger.info("[GsvmoveService] 已生成语音文件: %s", output_path)
            return output_path
        except Exception as e:
            logger.error("[GsvmoveService] TTS 推理失败: %s", e)
            return None

    def cleanup(self):
        self._ec.unsubscribe(EventType.APP_PRE_START, self._on_app_pre_start)
        self._ec.unsubscribe(EventType.AI_VOICE_REQUEST, self._on_ai_voice_request)
        self._worker_stop.set()
        self._request_queue.put(None)
        try:
            self._session.close()
        except Exception:
            pass

        with self._proc_lock:
            proc = self._process
            self._process = None
            self._started_by_app = False

        if self._shutdown_started_service(proc):
            return
        logger.warning("[GsvmoveService] 结束 GSVmove 进程失败：常规终止与兜底清理均未成功")

    def shutdown_service_process(self) -> bool:
        with self._proc_lock:
            proc = self._process
            self._process = None
            self._started_by_app = False
        stopped = self._shutdown_started_service(proc)
        return stopped

    def _shutdown_started_service(self, proc: subprocess.Popen | None) -> bool:
        if proc is not None and proc.poll() is None:
            if self._terminate_process_tree(proc.pid, force=False):
                logger.info("[GsvmoveService] 已结束 GSVmove 后台进程")
            elif self._terminate_process_tree(proc.pid, force=True):
                logger.info("[GsvmoveService] 已强制结束 GSVmove 后台进程")

        fallback_pids = self._find_gsvmove_service_pids()
        if proc is not None and getattr(proc, "pid", None):
            fallback_pids.discard(int(proc.pid))

        for pid in sorted(fallback_pids):
            if self._terminate_process_tree(pid, force=True):
                logger.info("[GsvmoveService] 已强制结束残留 GSVmove 服务进程 pid=%s", pid)
        if proc is not None or fallback_pids:
            time.sleep(0.8)

        proc_alive = bool(proc is not None and proc.poll() is None)
        remaining_pids = self._find_gsvmove_service_pids()
        if proc is not None and getattr(proc, "pid", None):
            remaining_pids.discard(int(proc.pid))
        return (not proc_alive) and (not remaining_pids)

    def _terminate_process_tree(self, pid: int, force: bool) -> bool:
        try:
            if os.name == "nt":
                cmd = ["taskkill", "/PID", str(pid), "/T"]
                if force:
                    cmd.append("/F")
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    check=False,
                )
                return result.returncode == 0
        except Exception as e:
            logger.debug("[GsvmoveService] taskkill 结束进程树失败 pid=%s force=%s err=%s", pid, force, e)
        return False

    def _find_gsvmove_service_pids(self) -> set[int]:
        candidates: set[int] = set()
        command = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -match 'GSVmove.+api\\.py' -and $_.CommandLine -match '--port\\s+9880' } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit():
                    candidates.add(int(line))
        except Exception as e:
            logger.debug("[GsvmoveService] 枚举 GSVmove 进程失败: %s", e)
        return candidates


_instance: GsvmoveService | None = None


def get_gsvmove_service() -> GsvmoveService:
    global _instance
    if _instance is None:
        ensure_shared_config_ready()
        _instance = GsvmoveService()
    return _instance


def cleanup_gsvmove_service():
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
