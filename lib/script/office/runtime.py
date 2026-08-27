"""Managed JSONL subprocess for the bundled DeepSeek Harness profile."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from config.user_storage_paths import get_user_state_dir
from lib.core.compute_hub import get_compute_hub
from lib.core import dsh_runtime_contract as dsh_config
from lib.core.logger import get_logger


logger = get_logger(__name__)

DSH_VERSION = dsh_config.DSH_VERSION
NODE_VERSION = dsh_config.NODE_VERSION_TEXT
NPM_VERSION = dsh_config.NPM_VERSION
PROTOCOL = "fsv-office/1"
OFFICE_SYSTEM_PROMPT_RESOURCE = Path("resc") / "agent" / "office_system_prompt.txt"
OFFICE_SKILL_ROOT_RESOURCE = Path("resc") / "agent"


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def runtime_root() -> Path:
    return dsh_config.dsh_runtime_root(project_root())


def office_system_prompt_path() -> Path:
    return project_root() / OFFICE_SYSTEM_PROMPT_RESOURCE


def office_skill_root() -> Path:
    return project_root() / OFFICE_SKILL_ROOT_RESOURCE


def load_office_system_prompt() -> str:
    path = office_system_prompt_path()
    try:
        prompt = path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise RuntimeError(f"办公系统提示词资源无法读取：{path}") from exc
    if not prompt:
        raise RuntimeError(f"办公系统提示词资源为空：{path}")
    return prompt


def bundled_node_executable() -> Path:
    if os.name == "nt":
        return dsh_config.node_executable(project_root())
    return dsh_config.node_root(project_root()) / "bin" / "node"


def resolve_node_executable() -> str | None:
    bundled = bundled_node_executable()
    if bundled.is_file():
        return str(bundled)
    system_node = shutil.which("node")
    if not system_node:
        return None
    try:
        result = subprocess.run(
            [system_node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return system_node if result.returncode == 0 and result.stdout.strip() == NODE_VERSION else None


def dsh_entry_path() -> Path:
    return dsh_config.dsh_entry_path(project_root())


def normalize_openai_base_url(value: object) -> str:
    """Convert a full chat-completions endpoint to the SDK base URL."""
    base_url = str(value or "").strip().rstrip("/")
    suffix = "/chat/completions"
    if base_url.lower().endswith(suffix):
        base_url = base_url[:-len(suffix)].rstrip("/")
    return base_url


def runtime_readiness_error() -> str:
    source_error = dsh_config.runtime_source_error(project_root())
    if source_error:
        return f"{source_error}，请重新解压程序包"
    try:
        load_office_system_prompt()
    except RuntimeError as exc:
        return str(exc)
    node = resolve_node_executable()
    if node is None:
        return f"缺少 Node {NODE_VERSION}，请重新运行“安装依赖.bat”"
    installed_error = dsh_config.installed_runtime_error(project_root())
    if installed_error:
        return f"{installed_error}，请重新运行“安装依赖.bat”"
    if bundled_node_executable().is_file():
        npm_cli = dsh_config.npm_cli_path(project_root())
        if not npm_cli.is_file():
            return f"npm {NPM_VERSION} 运行时不完整，请重新运行“安装依赖.bat”"
    return ""


def _hidden_process_kwargs() -> dict:
    if os.name != "nt":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}


class DshOfficeRuntime:
    def __init__(self, event_callback: Callable[[dict], None]) -> None:
        self._event_callback = event_callback
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._reader_future = None
        self._stderr_handle = None
        self._fingerprint: tuple[str, str, str, str] | None = None
        self._cleaning = False
        self._closed = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self, *, workspace: Path, base_url: str, model: str, api_key: str) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("DSH 办公运行时已清理")
        readiness = runtime_readiness_error()
        if readiness:
            raise RuntimeError(readiness)
        workspace = Path(workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        base_url = normalize_openai_base_url(base_url)
        model = str(model or "").strip()
        api_key = str(api_key or "").strip()
        if not base_url or not model or not api_key:
            raise RuntimeError("办公模式需要完整的接口地址、模型和 API key")
        system_prompt = load_office_system_prompt()
        prompt_digest = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        fingerprint = (str(workspace), base_url, model, prompt_digest)

        with self._lock:
            if self._closed:
                raise RuntimeError("DSH 办公运行时已清理")
            if self.running and self._fingerprint == fingerprint:
                self.send({"type": "configure", "apiKey": api_key})
                return
            if self._process is not None:
                self._stop_locked()
            self._cleaning = False
            dsh_home = self._provision_profile()
            sessions = get_user_state_dir("office", "dsh-sessions")
            sessions.mkdir(parents=True, exist_ok=True)
            log_path = get_user_state_dir("office", "dsh-runtime.log")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_handle = log_path.open("a", encoding="utf-8", errors="replace")
            env = os.environ.copy()
            env.update({
                "DSH_HOME": str(dsh_home),
                "DSH_BUNDLED_SKILL_DIR": str(office_skill_root()),
                "DSH_TELEMETRY_DISABLED": "1",
                "FSV_OFFICE_BASE_URL": base_url,
                "FSV_OFFICE_MODEL": model,
                "FSV_OFFICE_SESSION_ROOT": str(sessions),
                "FSV_OFFICE_SYSTEM_PROMPT": system_prompt,
            })
            command = [
                str(resolve_node_executable()),
                str(dsh_entry_path()),
                "--profile",
                "fsv-office",
            ]
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(workspace),
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr_handle,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    **_hidden_process_kwargs(),
                )
            except Exception:
                stderr_handle.close()
                raise
            self._process = process
            self._stderr_handle = stderr_handle
            self._fingerprint = fingerprint
            self._reader_future = get_compute_hub().submit_io(self._read_stdout, process)
        self.send({"type": "configure", "apiKey": api_key})

    def send(self, payload: dict) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            with self._lock:
                process = self._process
                stream = process.stdin if process is not None else None
                if process is None or process.poll() is not None or stream is None:
                    raise RuntimeError("DSH 办公运行时未启动")
            try:
                stream.write(line)
                stream.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise RuntimeError("DSH 办公运行时连接已断开") from exc

    def _read_stdout(self, process: subprocess.Popen) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for raw_line in iter(stream.readline, ""):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except (ValueError, TypeError):
                    logger.debug("[DshOfficeRuntime] DSH diagnostic: %s", line[:500])
                    continue
                if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
                    logger.debug("[DshOfficeRuntime] Ignored non-bridge JSON output")
                    continue
                self._event_callback(payload)
        finally:
            return_code = process.poll()
            with self._lock:
                owned = self._process is process
                cleaning = self._cleaning
                if owned:
                    self._process = None
                    self._fingerprint = None
                    handle, self._stderr_handle = self._stderr_handle, None
                    if handle is not None:
                        handle.close()
            if owned and not cleaning:
                self._event_callback({
                    "protocol": PROTOCOL,
                    "type": "process_exit",
                    "returnCode": return_code,
                })

    def _provision_profile(self) -> Path:
        source_profile = runtime_root() / "profile"
        source_bridge = runtime_root() / "bridge"
        dsh_home = get_user_state_dir("office", "dsh-home")
        profile = dsh_home / "profiles" / "fsv-office"
        bridge = profile / "node_modules" / "@fsv" / "dsh-office-bridge"
        bridge.mkdir(parents=True, exist_ok=True)
        for name in ("package.json", "cordis.patch.yml"):
            shutil.copy2(source_profile / name, profile / name)
        for name in ("package.json", "index.mjs", "credentials.mjs"):
            shutil.copy2(source_bridge / name, bridge / name)
        return dsh_home

    def cleanup(self) -> None:
        with self._lock:
            if self._closed and self._process is None:
                return
            self._closed = True
            self._cleaning = True
            self._stop_locked()

    def _stop_locked(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                self.send({"type": "shutdown"})
                process.wait(timeout=3)
            except (RuntimeError, subprocess.TimeoutExpired):
                self._terminate_process_tree(process)
        self._process = None
        self._fingerprint = None
        handle, self._stderr_handle = self._stderr_handle, None
        if handle is not None:
            handle.close()

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    check=False,
                    **_hidden_process_kwargs(),
                )
                return
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
