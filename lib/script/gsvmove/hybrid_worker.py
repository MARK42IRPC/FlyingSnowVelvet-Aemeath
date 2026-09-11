"""Persistent isolated ONNX voice workers and their synchronous facade."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path
from queue import Empty, Queue

class VoiceWorkerError(RuntimeError):
    pass


def _site_packages_for_python(python_path: Path) -> Path | None:
    """Resolve a conventional Lib/site-packages directory for an interpreter."""
    # Preserve the caller's spelling (including Windows 8.3 temp paths).
    # These paths are placed in child-process environments and must remain
    # consistent with the paths used to construct the temporary fixture.
    executable = Path(python_path)
    roots = [executable.parent]
    if executable.parent.name.casefold() in {"scripts", "bin"}:
        roots.insert(0, executable.parent.parent)
    for root in roots:
        candidate = root / "Lib" / "site-packages"
        if candidate.is_dir():
            return candidate
    return None


def _bundled_python_path(app_root: Path) -> Path | None:
    candidate = Path(app_root).resolve().parent / "runtime" / "python311" / "python.exe"
    return candidate if candidate.is_file() else None


def _terminate_worker_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10.0,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            process.wait(timeout=3.0)
            return
        except Exception:
            pass
    try:
        process.terminate()
        process.wait(timeout=3.0)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


class VoiceWorkerRuntime:
    """Reuse one low-priority subprocess for sequential synthesis requests."""

    START_TIMEOUT_SECONDS = 180.0
    REQUEST_TIMEOUT_SECONDS = 360.0

    def __init__(
        self,
        package_root: Path,
        output_root: Path,
        *,
        provider: str,
        python_path: Path,
        isolate_user_site: bool = False,
        module_overlay: Path | tuple[Path, ...] | list[Path] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if provider not in {"cpu", "hybrid", "cuda"}:
            raise ValueError(f"不支持的 ONNX Worker Provider：{provider}")
        self.provider = provider
        self.package_root = Path(package_root).resolve()
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._messages: Queue[dict | None] = Queue()
        self._stderr_tail: deque[str] = deque(maxlen=80)
        self._closed = False
        self._cancel_event = cancel_event

        worker_script = Path(__file__).resolve()
        project_root = worker_script.parents[3]
        if module_overlay is None:
            overlays: tuple[Path, ...] = ()
        elif isinstance(module_overlay, (tuple, list)):
            overlays = tuple(Path(item).resolve() for item in module_overlay)
        else:
            overlays = (Path(module_overlay).resolve(),)
        for overlay in overlays:
            if not overlay.is_dir():
                raise VoiceWorkerError(f"ONNX Worker 覆盖层不存在：{overlay}")

        # Make the runtime self-contained: DirectML's overlay comes first,
        # followed by the package's common site-packages and project code.
        site_paths: list[Path] = list(overlays)
        for candidate in (
            _site_packages_for_python(Path(python_path)),
            project_root.parent / "runtime" / "python311" / "Lib" / "site-packages",
            _site_packages_for_python(Path(sys.executable)),
        ):
            if candidate is not None:
                candidate = Path(candidate).resolve()
                if candidate.is_dir() and candidate not in site_paths:
                    site_paths.append(candidate)
        env = os.environ.copy()
        for variable in (
            "PYTHONHOME",
            "PYTHONPATH",
            "PYTHONSTARTUP",
            "PYTHONUSERBASE",
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
            "CONDA_DEFAULT_ENV",
            "NODE_OPTIONS",
            "NODE_PATH",
            "NPM_CONFIG_PREFIX",
            "DSH_HOME",
            "DSH_RUNTIME_ROOT",
            "FSV_OFFICE_HOME",
            "FSV_OFFICE_RUNTIME",
            "OPENSSL_CONF",
            "SSL_CERT_DIR",
            "SSL_CERT_FILE",
            "PLAYWRIGHT_BROWSERS_PATH",
        ):
            env.pop(variable, None)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONNOUSERSITE"] = "1"
        path_entries = [
            entry
            for entry in str(env.get("PATH") or "").split(os.pathsep)
            if "pyqt5\\qt5\\bin" not in entry.replace("/", "\\").lower()
        ]
        env["PATH"] = os.pathsep.join(path_entries)
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        )
        bootstrap = (
            "import json,runpy,sys;"
            "project,paths,worker=sys.argv[1:4];"
            "sys.path[:0]=[path for path in json.loads(paths) if path];"
            "sys.path.insert(len(json.loads(paths)),project);"
            "sys.argv=[worker,*sys.argv[4:]];"
            "runpy.run_path(worker,run_name='__main__')"
        )
        bundled_python = _bundled_python_path(project_root)
        if provider in {"cpu", "cuda"} and bundled_python is not None:
            python_path = bundled_python
        try:
            self._process = subprocess.Popen(
                [
                    str(python_path),
                    "-I",
                    "-u",
                    "-X",
                    "utf8",
                    "-c",
                    bootstrap,
                    str(project_root),
                    json.dumps([str(path) for path in site_paths], ensure_ascii=False),
                    str(worker_script),
                    "--worker",
                    "--provider",
                    provider,
                    "--package-root",
                    str(self.package_root),
                    "--output-root",
                    str(self.output_root),
                ],
                cwd=str(project_root),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise VoiceWorkerError(f"无法启动 ONNX Worker：{exc}") from exc

        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            daemon=True,
            name=f"onnx-{provider}-worker-stdout",
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            daemon=True,
            name=f"onnx-{provider}-worker-stderr",
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

        try:
            ready = self._next_message(self.START_TIMEOUT_SECONDS)
            if ready.get("type") != "ready":
                raise VoiceWorkerError(str(ready.get("error") or "ONNX Worker 启动失败"))
            if ready.get("provider") != provider:
                raise VoiceWorkerError("ONNX Worker Provider 响应不匹配")
        except Exception:
            self.close()
            raise

    def _read_stdout(self) -> None:
        stream = self._process.stdout
        if stream is None:
            self._messages.put(None)
            return
        try:
            for line in stream:
                try:
                    payload = json.loads(line)
                except (TypeError, ValueError):
                    self._stderr_tail.append(f"非协议输出: {line.strip()}")
                    continue
                if isinstance(payload, dict):
                    self._messages.put(payload)
        finally:
            self._messages.put(None)

    def _read_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        for line in stream:
            text = line.rstrip()
            if text:
                self._stderr_tail.append(text)

    def _error_detail(self) -> str:
        return " | ".join(tuple(self._stderr_tail)[-6:])

    def _next_message(self, timeout: float) -> dict:
        deadline = time.monotonic() + max(0.1, float(timeout))
        while True:
            if self._cancel_event is not None and self._cancel_event.is_set():
                raise VoiceWorkerError("ONNX Worker 操作已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                detail = self._error_detail()
                suffix = f"：{detail}" if detail else ""
                raise VoiceWorkerError(f"ONNX Worker 响应超时{suffix}")
            try:
                message = self._messages.get(timeout=min(0.2, remaining))
                break
            except Empty:
                continue
        if message is None:
            code = self._process.poll()
            detail = self._error_detail()
            suffix = f"：{detail}" if detail else ""
            raise VoiceWorkerError(f"ONNX Worker 已退出（code={code}）{suffix}")
        return message

    def _send(self, payload: dict) -> None:
        if self._closed or self._process.poll() is not None:
            raise VoiceWorkerError("ONNX Worker 未运行")
        stream = self._process.stdin
        if stream is None:
            raise VoiceWorkerError("ONNX Worker 输入通道不可用")
        try:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stream.flush()
        except (OSError, ValueError) as exc:
            raise VoiceWorkerError(f"无法向 ONNX Worker 发送请求：{exc}") from exc

    def synthesize_to_file(self, payload: dict, output_path: Path) -> Path:
        destination = Path(output_path).resolve()
        try:
            destination.relative_to(self.output_root)
        except ValueError as exc:
            raise VoiceWorkerError("ONNX Worker 输出路径越界") from exc

        request_id = uuid.uuid4().hex
        with self._write_lock:
            self._send({
                "type": "synthesize",
                "id": request_id,
                "payload": dict(payload),
                "output_name": destination.name,
            })
            response = self._next_message(self.REQUEST_TIMEOUT_SECONDS)
        if response.get("id") != request_id:
            raise VoiceWorkerError("ONNX Worker 响应序号不匹配")
        if not response.get("ok"):
            raise VoiceWorkerError(str(response.get("error") or "ONNX 推理失败"))
        if not destination.is_file() or destination.stat().st_size <= 44:
            raise VoiceWorkerError("ONNX Worker 未生成有效 WAV 文件")
        return destination

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        process = getattr(self, "_process", None)
        if process is None:
            return
        if process.poll() is None:
            try:
                stream = process.stdin
                if stream is not None:
                    stream.write('{"type":"shutdown"}\n')
                    stream.flush()
                process.wait(timeout=5.0)
            except Exception:
                _terminate_worker_process_tree(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


class CpuVoiceWorkerRuntime(VoiceWorkerRuntime):
    def __init__(self, package_root: Path, output_root: Path) -> None:
        super().__init__(
            package_root,
            output_root,
            provider="cpu",
            python_path=Path(sys.executable),
        )


class HybridVoiceWorkerRuntime(VoiceWorkerRuntime):
    def __init__(self, package_root: Path, output_root: Path) -> None:
        from config.voice_runtime import (
            get_directml_worker_python_path,
            get_directml_worker_site_packages,
            is_directml_runtime_ready,
        )

        if not is_directml_runtime_ready():
            raise VoiceWorkerError("DirectML 混合推理环境未安装，请重新运行安装依赖")
        super().__init__(
            package_root,
            output_root,
            provider="hybrid",
            python_path=get_directml_worker_python_path(),
            isolate_user_site=True,
            module_overlay=get_directml_worker_site_packages(),
        )


class CudaVoiceWorkerRuntime(VoiceWorkerRuntime):
    """N-card acceleration backed by the self-written CUDA runtime.

    The native backend needs nothing but the NVIDIA display driver and replaces
    the old multi-gigabyte downloaded ORT bundle.  It is a single DLL shipped
    with the release, so unlike DirectML there is no per-machine environment to
    install and no site-packages overlay to mount.

    The parent process only checks that the DLL is present; the worker performs
    the real driver check and reports failures through the startup handshake.
    """

    def __init__(self, package_root: Path, output_root: Path) -> None:
        from lib.core.voice_runtime_contract import resolve_cuda_voice_runtime_path

        library = resolve_cuda_voice_runtime_path()
        if library is None:
            raise VoiceWorkerError(
                "自研 CUDA 推理端不可用：缺少 fsv_cuda_voice_runtime.dll"
            )
        self.library_path = library
        super().__init__(
            package_root,
            output_root,
            provider="cuda",
            python_path=Path(sys.executable),
        )


def _write_protocol(stream, payload: dict) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def _resolve_worker_output(output_root: Path, name: object) -> Path:
    filename = Path(str(name or "")).name
    if not filename or not filename.lower().endswith(".wav"):
        raise ValueError("Worker 输出文件名无效")
    destination = (output_root / filename).resolve()
    destination.relative_to(output_root)
    return destination


def _run_worker(package_root: Path, output_root: Path, provider: str) -> int:
    protocol_out = sys.stdout
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from lib.script.gsvmove.onnx_runtime import OnnxVoiceRuntime

            runtime = OnnxVoiceRuntime(package_root, provider=provider)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        detail = str(exc).strip() or repr(exc)
        _write_protocol(protocol_out, {"type": "error", "error": detail})
        return 2

    _write_protocol(protocol_out, {"type": "ready", "provider": provider})
    try:
        for line in sys.stdin:
            request = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Worker 请求格式无效")
                if request.get("type") == "shutdown":
                    break
                if request.get("type") != "synthesize":
                    raise ValueError("Worker 请求类型无效")
                request_id = str(request.get("id") or "")
                destination = _resolve_worker_output(output_root, request.get("output_name"))
                with contextlib.redirect_stdout(sys.stderr):
                    runtime.synthesize_to_file(dict(request.get("payload") or {}), destination)
                _write_protocol(protocol_out, {"id": request_id, "ok": True})
            except Exception as exc:
                _write_protocol(protocol_out, {
                    "id": str(request.get("id") or "") if isinstance(request, dict) else "",
                    "ok": False,
                    "error": str(exc),
                })
    finally:
        with contextlib.redirect_stdout(sys.stderr):
            runtime.close()
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--provider", choices=("cpu", "hybrid", "cuda"))
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if (
        not args.worker
        or args.provider is None
        or args.package_root is None
        or args.output_root is None
    ):
        raise SystemExit("This module is an internal ONNX worker")
    return _run_worker(args.package_root, args.output_root, args.provider)


if __name__ == "__main__":
    raise SystemExit(main())
