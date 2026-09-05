"""Transactional installer for the pinned CUDA voice Runtime Bundle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import voice_runtime_contract as contract
from .cuda_runtime_bundle import safe_extract_zip, validate_bundle_tree
from .cuda_runtime_cleanup import cleanup_obsolete_cuda_runtime_artifacts


ProgressCallback = Callable[[str, int, int, str], None]
InfoCallback = Callable[[str], None]
VoiceProbe = Callable[[Path, threading.Event], tuple[bool, str]]

_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_PROCESS_POLL_SECONDS = 0.2


class CudaRuntimeInstallError(RuntimeError):
    """Raised when the optional CUDA runtime cannot be installed."""


class CudaRuntimeInstallCancelled(CudaRuntimeInstallError):
    """Raised after a user cancellation has stopped the active operation."""


@dataclass(frozen=True)
class CudaRuntimeInstallResult:
    runtime_root: Path
    archive_bytes: int
    installed_bytes: int
    bundle_id: str


def _hidden_process_kwargs() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=3.0)
        return
    except Exception:
        pass
    try:
        process.kill()
        process.wait(timeout=2.0)
    except Exception:
        pass


def _parse_probe_payload(output: str) -> dict | None:
    for line in reversed(str(output or "").splitlines()):
        value = line.strip()
        if not value:
            continue
        try:
            payload = json.loads(value)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and "providers" in payload:
            return payload
    return None


_CUDA_PROBE_CODE = r'''
import json
import struct
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort

runtime_root = Path(sys.executable).resolve().parent.parent
marker = json.loads((runtime_root / "runtime.json").read_text(encoding="utf-8"))
relative_dll = str(marker.get("dll_directory") or "").replace("\\", "/")
dll_dir = (runtime_root / Path(*relative_dll.split("/"))).resolve()
dll_dir.relative_to(runtime_root)
if not dll_dir.is_dir():
    raise RuntimeError("CUDA Bundle DLL directory is missing")
preload = getattr(ort, "preload_dlls", None)
if callable(preload):
    preload(directory=str(dll_dir))

from onnx import TensorProto, helper
node = helper.make_node("Identity", ["x"], ["y"])
graph = helper.make_graph(
    [node],
    "cuda_probe",
    [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])],
    [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1])],
)
model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
model.ir_version = 10
session = ort.InferenceSession(
    model.SerializeToString(),
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)
session.run(None, {"x": np.ones((1,), dtype=np.float32)})
print(json.dumps({
    "python": list(sys.version_info[:2]),
    "bits": struct.calcsize("P") * 8,
    "version": ort.__version__,
    "providers": session.get_providers(),
}))
'''


def _probe_result(stdout: str, stderr: str, return_code: int) -> tuple[bool, str]:
    if int(return_code) != 0:
        detail = " | ".join(
            line.strip()
            for line in str(stderr or stdout or "").splitlines()[-8:]
            if line.strip()
        )
        return False, detail or f"CUDA 探测进程返回 {return_code}"
    payload = _parse_probe_payload(stdout)
    if payload is None:
        return False, "CUDA 环境探测结果无法解析"
    if payload.get("python") != [3, 11] or payload.get("bits") != 64:
        return False, "CUDA Worker 仅支持 64 位 Python 3.11"
    if payload.get("version") != contract.CUDA_RUNTIME_VERSION:
        return False, f"CUDA 运行库版本不匹配：{payload.get('version')}"
    providers = tuple(payload.get("providers") or ())
    if "CUDAExecutionProvider" not in providers:
        return False, f"CUDAExecutionProvider 不可用：{list(providers)}"
    return True, ""


def probe_cuda_runtime_session(
    runtime_root: Path | None = None,
    *,
    timeout: float = 90.0,
) -> tuple[bool, str]:
    """Run a real CUDA Session in the installed isolated interpreter."""

    root = Path(runtime_root) if runtime_root is not None else contract.get_cuda_runtime_root()
    if not contract.is_cuda_runtime_ready(root):
        return False, "CUDA Bundle 静态完整性检查未通过"
    try:
        result = subprocess.run(
            [str(contract.get_cuda_python_path(root)), "-c", _CUDA_PROBE_CODE],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(timeout)),
            check=False,
            **_hidden_process_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"CUDA Session 探测失败：{exc}"
    return _probe_result(result.stdout or "", result.stderr or "", result.returncode)


def has_nvidia_gpu(*, timeout: float = 3.0) -> bool:
    """Return whether the NVIDIA driver exposes a local GPU."""

    executable = shutil.which("nvidia-smi")
    if not executable:
        return False
    try:
        result = subprocess.run(
            [executable, "-L"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=max(0.5, float(timeout)),
            check=False,
            **_hidden_process_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(str(result.stdout or "").strip())


class CudaRuntimeInstaller:
    """Download, validate, probe and atomically activate one pinned Bundle."""

    def __init__(
        self,
        python_executable: Path | str,
        *,
        target_root: Path | None = None,
        urls: tuple[str, ...] | None = None,
        progress_callback: ProgressCallback | None = None,
        info_callback: InfoCallback | None = None,
        voice_probe: VoiceProbe | None = None,
    ) -> None:
        self.python_executable = Path(python_executable)
        self.target_root = Path(target_root or contract.get_cuda_runtime_root())
        self.urls = tuple(urls or contract.CUDA_RUNTIME_BUNDLE_URLS)
        self._progress_callback = progress_callback or (lambda *_args: None)
        self._info_callback = info_callback or (lambda _message: None)
        self._voice_probe = voice_probe
        self._cancel_event = threading.Event()
        self._process_lock = threading.Lock()
        self._active_process: subprocess.Popen | None = None
        self._response_lock = threading.Lock()
        self._active_response = None

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._response_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        with self._process_lock:
            process = self._active_process
        if process is not None:
            _terminate_process(process)

    def close(self) -> None:
        self.cancel()

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise CudaRuntimeInstallCancelled("N卡推理环境安装已取消")

    def _report(self, phase: str, current: int, total: int, message: str) -> None:
        self._progress_callback(
            str(phase),
            max(0, int(current)),
            max(0, int(total)),
            str(message),
        )

    def _report_install_ratio(
        self,
        current: int,
        total: int,
        start: int,
        end: int,
        message: str,
    ) -> None:
        ratio = 0.0 if total <= 0 else max(0.0, min(1.0, current / total))
        value = int(round(start + ((end - start) * ratio)))
        self._report("install", value, 1000, message)

    def _run_process(self, command: list[str], *, timeout: float) -> tuple[int, str, str]:
        self._check_cancelled()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_hidden_process_kwargs(),
        )
        with self._process_lock:
            self._active_process = process
        deadline = time.monotonic() + max(1.0, float(timeout))
        try:
            while True:
                self._check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_process(process)
                    raise CudaRuntimeInstallError(f"子进程执行超时：{command[0]}")
                try:
                    stdout, stderr = process.communicate(
                        timeout=min(_PROCESS_POLL_SECONDS, remaining)
                    )
                    return process.returncode or 0, stdout or "", stderr or ""
                except subprocess.TimeoutExpired:
                    continue
        finally:
            with self._process_lock:
                if self._active_process is process:
                    self._active_process = None
            if process.poll() is None:
                _terminate_process(process)

    def _check_python(self) -> None:
        code = "import json,struct,sys; print(json.dumps([list(sys.version_info[:2]), struct.calcsize('P')*8]))"
        code_value, stdout, stderr = self._run_process(
            [str(self.python_executable), "-c", code],
            timeout=30,
        )
        if code_value != 0:
            raise CudaRuntimeInstallError(stderr.strip() or "无法检查 Python 运行环境")
        try:
            payload = json.loads(stdout.strip().splitlines()[-1])
        except (IndexError, TypeError, ValueError) as exc:
            raise CudaRuntimeInstallError("Python 运行环境探测结果无法解析") from exc
        if payload != [[3, 11], 64]:
            raise CudaRuntimeInstallError("N卡推理环境仅支持 64 位 Python 3.11")

    def _download(self, url: str, destination: Path) -> tuple[int, str]:
        self._check_cancelled()
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"https", "http", "file"}:
            raise CudaRuntimeInstallError(f"不支持的下载协议：{parsed.scheme}")
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "FlyingSnowVelvetCudaRuntime/1.0",
                "Accept": "application/zip, application/octet-stream, */*",
            },
        )
        response = urllib.request.build_opener().open(request, timeout=90)
        with self._response_lock:
            self._active_response = response
        digest = hashlib.sha256()
        current = 0
        try:
            content_length = str(response.headers.get("Content-Length") or "").strip()
            total = int(content_length) if content_length.isdigit() else contract.CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES
            with response, destination.open("wb") as output:
                while True:
                    self._check_cancelled()
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    current += len(chunk)
                    self._report("download", current, total, "正在下载N卡推理环境")
        except Exception:
            self._check_cancelled()
            raise
        finally:
            with self._response_lock:
                if self._active_response is response:
                    self._active_response = None
            try:
                response.close()
            except Exception:
                pass
        return current, digest.hexdigest()

    def _download_bundle(self, workspace: Path) -> tuple[Path, int]:
        if not self.urls:
            raise CudaRuntimeInstallError("没有可用的N卡推理环境下载源")
        errors: list[str] = []
        archive = workspace / f"{contract.CUDA_RUNTIME_BUNDLE_ARCHIVE_NAME}.part"
        for index, url in enumerate(self.urls, start=1):
            self._check_cancelled()
            archive.unlink(missing_ok=True)
            host = urllib.parse.urlsplit(url).hostname or "本地文件"
            self._info_callback(f"正在连接下载源 {index}/{len(self.urls)}：{host}")
            self._report("download", 0, contract.CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES, "正在连接N卡推理环境下载源")
            try:
                size, digest = self._download(url, archive)
                self._check_cancelled()
                if size != contract.CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES:
                    raise CudaRuntimeInstallError(
                        f"下载大小不匹配：{size}/{contract.CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES} 字节"
                    )
                if digest.lower() != contract.CUDA_RUNTIME_BUNDLE_SHA256:
                    raise CudaRuntimeInstallError("下载文件 SHA-256 校验失败")
                return archive, size
            except CudaRuntimeInstallCancelled:
                raise
            except Exception as exc:
                errors.append(f"{host}：{exc}")
                archive.unlink(missing_ok=True)
        raise CudaRuntimeInstallError("所有下载源均失败：" + "；".join(errors))

    @staticmethod
    def _strip_venv_bootstrap_files(runtime_root: Path) -> None:
        site_packages = runtime_root / "Lib" / "site-packages"
        for pattern in ("pip", "pip-*", "setuptools", "setuptools-*", "pkg_resources"):
            for path in site_packages.glob(pattern):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
        for path in (runtime_root / "Scripts").glob("pip*"):
            path.unlink(missing_ok=True)

    def _write_marker(self, staging_root: Path, manifest: dict) -> None:
        marker = {
            "runtime": "onnxruntime-gpu",
            "version": contract.CUDA_RUNTIME_VERSION,
            "abi": contract.CUDA_RUNTIME_ABI,
            "provider": "CUDAExecutionProvider",
            "python_executable": str(self.python_executable),
            "source": "bundle",
            "bundle_format": contract.CUDA_RUNTIME_BUNDLE_FORMAT,
            "bundle_version": contract.CUDA_RUNTIME_BUNDLE_FORMAT_VERSION,
            "bundle_id": str(manifest.get("bundle_id") or ""),
            "archive_sha256": contract.CUDA_RUNTIME_BUNDLE_SHA256,
            "dll_directory": contract.CUDA_RUNTIME_BUNDLE_DLL_DIRECTORY,
            "required_dlls": list(contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS),
        }
        (staging_root / contract.CUDA_RUNTIME_MARKER_NAME).write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _probe_staging(self, staging_root: Path) -> None:
        code, stdout, stderr = self._run_process(
            [str(contract.get_cuda_python_path(staging_root)), "-c", _CUDA_PROBE_CODE],
            timeout=90,
        )
        ready, detail = _probe_result(stdout, stderr, code)
        if not ready:
            raise CudaRuntimeInstallError(f"CUDA Session 校验失败：{detail}")

    def _activate(self, staging_root: Path) -> None:
        target = self.target_root
        backup = target.with_name(f".{target.name}.previous-{uuid.uuid4().hex}")
        moved_old = False
        try:
            if target.exists():
                target.replace(backup)
                moved_old = True
            staging_root.replace(target)
            if not contract.is_cuda_runtime_ready(target):
                raise CudaRuntimeInstallError("激活后的 Bundle 静态完整性检查失败")
        except Exception:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if moved_old and backup.exists():
                backup.replace(target)
            raise
        finally:
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)

    def install(self) -> CudaRuntimeInstallResult:
        self._check_cancelled()
        self.target_root.parent.mkdir(parents=True, exist_ok=True)
        self._report("install", 0, 1000, "正在检查运行环境")
        self._check_python()

        cleanup = cleanup_obsolete_cuda_runtime_artifacts(
            self.target_root.parent,
            preserve_valid_runtime=True,
        )
        if cleanup.errors:
            self._info_callback("部分旧 CUDA 临时文件未能清理，将继续安装")

        required = (
            contract.CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES
            + contract.CUDA_RUNTIME_BUNDLE_PAYLOAD_BYTES
            + contract.CUDA_RUNTIME_BUNDLE_STAGING_OVERHEAD_BYTES
        )
        available = shutil.disk_usage(self.target_root.parent).free
        if available < required:
            raise CudaRuntimeInstallError(
                f"临时空间不足：需要 {required} 字节，可用 {available} 字节"
            )

        token = uuid.uuid4().hex
        workspace = self.target_root.with_name(f".{self.target_root.name}.bundle-{token}")
        staging_root = self.target_root.with_name(f".{self.target_root.name}.installing-{token}")
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(staging_root, ignore_errors=True)
        workspace.mkdir(parents=True)
        try:
            archive, archive_bytes = self._download_bundle(workspace)
            self._report("install", 30, 1000, "下载完成，正在安全解压")
            extract_root = workspace / "bundle"
            safe_extract_zip(
                archive,
                extract_root,
                progress_callback=lambda current, total: self._report_install_ratio(
                    current, total, 30, 430, "正在解压N卡推理环境"
                ),
                cancellation_check=self._check_cancelled,
            )
            self._report("install", 430, 1000, "正在逐文件校验运行库")
            manifest = validate_bundle_tree(
                extract_root,
                progress_callback=lambda current, total: self._report_install_ratio(
                    current, total, 430, 720, "正在校验N卡推理环境"
                ),
                cancellation_check=self._check_cancelled,
            )

            self._report("install", 730, 1000, "正在创建隔离推理环境")
            code, _stdout, stderr = self._run_process(
                [
                    str(self.python_executable),
                    "-m",
                    "venv",
                    "--system-site-packages",
                    str(staging_root),
                ],
                timeout=180,
            )
            if code != 0:
                raise CudaRuntimeInstallError(stderr.strip() or "创建隔离推理环境失败")

            self._check_cancelled()
            payload_site = (
                extract_root
                / contract.CUDA_RUNTIME_BUNDLE_PAYLOAD_ROOT
                / "Lib"
                / "site-packages"
            )
            staging_site = staging_root / "Lib" / "site-packages"
            if not payload_site.is_dir():
                raise CudaRuntimeInstallError("Bundle 缺少 Lib/site-packages")
            shutil.rmtree(staging_site, ignore_errors=True)
            staging_site.parent.mkdir(parents=True, exist_ok=True)
            payload_site.replace(staging_site)
            for name in ("bundle.json", "SHA256SUMS.txt", "THIRD_PARTY_NOTICES.txt"):
                source = extract_root / name
                if source.is_file():
                    shutil.copy2(source, staging_root / name)
            self._strip_venv_bootstrap_files(staging_root)
            self._write_marker(staging_root, manifest)

            self._report("install", 820, 1000, "正在验证 CUDA Session")
            self._probe_staging(staging_root)
            if self._voice_probe is not None:
                self._check_cancelled()
                self._report("install", 900, 1000, "正在验证中英文语音模型")
                ready, detail = self._voice_probe(
                    contract.get_cuda_python_path(staging_root),
                    self._cancel_event,
                )
                self._check_cancelled()
                if not ready:
                    raise CudaRuntimeInstallError(f"真实语音推理校验失败：{detail}")

            self._report("install", 980, 1000, "正在激活N卡推理环境")
            self._activate(staging_root)
            self._report("install", 1000, 1000, "N卡推理环境安装完成")
            return CudaRuntimeInstallResult(
                runtime_root=self.target_root,
                archive_bytes=archive_bytes,
                installed_bytes=contract.CUDA_RUNTIME_BUNDLE_PAYLOAD_BYTES,
                bundle_id=str(manifest.get("bundle_id") or ""),
            )
        except CudaRuntimeInstallCancelled:
            raise
        except Exception as exc:
            raise CudaRuntimeInstallError(str(exc)) from exc
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
            shutil.rmtree(staging_root, ignore_errors=True)


__all__ = [
    "CudaRuntimeInstallCancelled",
    "CudaRuntimeInstallError",
    "CudaRuntimeInstallResult",
    "CudaRuntimeInstaller",
    "has_nvidia_gpu",
    "probe_cuda_runtime_session",
]
