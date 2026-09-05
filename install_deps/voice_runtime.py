"""Optional CUDA and DirectML voice runtime installation."""

import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import uuid
import wave
import zipfile
from pathlib import Path

from lib.core import voice_runtime_contract as directml_config
from lib.core.cuda_runtime_bundle import (
    CudaBundleError,
    safe_extract_zip,
    validate_bundle_tree,
)

from .bootstrap import _get_version, _run
from .catalog import PROJECT_ROOT, PYPI_MIRRORS, TARGET_PYTHON
from .console import _print_warn
from .dependencies import _summarize_pip_failure
from .resources import _rmtree_if_exists, _stream_download_with_progress


def _directml_runtime_probe(runtime_python: Path) -> tuple[bool, str]:
    code = (
        "import json, struct, sys, onnxruntime as ort; "
        "payload={'python': list(sys.version_info[:2]), "
        "'bits': struct.calcsize('P') * 8, 'version': ort.__version__, "
        "'providers': ort.get_available_providers()}; "
        "print(json.dumps(payload))"
    )
    result = _run([str(runtime_python), "-c", code], timeout=60)
    if result is None or result.returncode != 0:
        detail = _summarize_pip_failure(result.stdout if result is not None else "")
        return False, detail
    payload = _parse_probe_payload(result.stdout or "")
    if payload is None:
        return False, "DirectML 环境探测结果无法解析"
    if payload.get("python") != [3, 11] or payload.get("bits") != 64:
        return False, "DirectML Worker 仅支持 64 位 Python 3.11"
    if payload.get("version") != directml_config.DIRECTML_RUNTIME_VERSION:
        return False, f"DirectML 运行库版本不匹配：{payload.get('version')}"
    if "DmlExecutionProvider" not in set(payload.get("providers") or ()):
        return False, f"DmlExecutionProvider 不可用：{payload.get('providers')}"
    return True, ""


def _bundled_directml_wheels() -> tuple[Path, ...]:
    """Return compatible DirectML wheels shipped inside an offline payload."""
    wheel_root = PROJECT_ROOT / "resc" / "onnxruntime-directml"
    if not wheel_root.is_dir():
        return ()
    prefix = f"onnxruntime_directml-{directml_config.DIRECTML_RUNTIME_VERSION}-"
    suffix = "-cp311-cp311-win_amd64.whl"
    return tuple(
        path
        for path in sorted(wheel_root.glob("onnxruntime_directml-*.whl"))
        if path.name.lower().startswith(prefix.lower())
        and path.name.lower().endswith(suffix)
    )


def _directml_pip_command(
    staging_python: Path,
    requirement: str,
    *,
    local: bool,
) -> list[str]:
    command = [
        str(staging_python),
        "-m",
        "pip",
        "install",
        requirement,
        "--no-deps",
        "--disable-pip-version-check",
        "--progress-bar",
        "off",
    ]
    if local:
        command.append("--no-index")
    return command

def ensure_directml_hybrid_runtime(python_exe, mirrors) -> bool:
    print("\n  准备 DirectML GPU 混合推理环境...")
    version = _get_version(python_exe)
    architecture = _run(
        [python_exe, "-c", "import struct; print(struct.calcsize('P') * 8)"],
        timeout=30,
    )
    if (
        version[:2] != TARGET_PYTHON
        or architecture is None
        or architecture.returncode != 0
        or (architecture.stdout or "").strip() != "64"
    ):
        _print_warn("  DirectML Worker 仅支持 64 位 Python 3.11，已跳过")
        return False

    target_root = directml_config.get_directml_runtime_root()
    runtime_python = directml_config.get_directml_python_path()
    if directml_config.is_directml_runtime_ready():
        ready, detail = _directml_runtime_probe(runtime_python)
        if ready:
            print(f"  DirectML 混合推理环境已存在: {target_root}")
            return True
        _print_warn(f"  现有 DirectML 环境无效，将重新安装：{detail}")

    staging_root = target_root.with_name(f".{target_root.name}.installing")
    _rmtree_if_exists(staging_root, ignore_errors=True)
    target_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        created = _run(
            [python_exe, "-m", "venv", "--system-site-packages", str(staging_root)],
            timeout=180,
        )
        if created is None or created.returncode != 0:
            detail = _summarize_pip_failure(created.stdout if created is not None else "")
            raise RuntimeError(f"创建隔离环境失败：{detail}")

        staging_python = staging_root / "Scripts" / "python.exe"
        sources = list(mirrors or ()) or [PYPI_MIRRORS[-1]]
        last_detail = "没有可用的 DirectML wheel 或 pip 镜像"
        installed = False
        for wheel in _bundled_directml_wheels():
            result = _run(
                _directml_pip_command(staging_python, str(wheel), local=True),
                timeout=600,
            )
            if result is not None and result.returncode == 0:
                installed = True
                print(f"  使用内置 DirectML wheel：{wheel.name}")
                break
            last_detail = (
                f"内置 wheel {wheel.name}："
                f"{_summarize_pip_failure(result.stdout if result is not None else '')}"
            )
        for mirror in sources:
            if installed:
                break
            command = _directml_pip_command(
                staging_python,
                directml_config.DIRECTML_RUNTIME_REQUIREMENT,
                local=False,
            )
            command.extend(("-i", mirror["url"], "--trusted-host", mirror["host"]))
            result = _run(command, timeout=600)
            if result is not None and result.returncode == 0:
                installed = True
                break
            last_detail = f"{mirror['name']}：{_summarize_pip_failure(result.stdout if result is not None else '')}"
        if not installed:
            raise RuntimeError(f"安装 {directml_config.DIRECTML_RUNTIME_REQUIREMENT} 失败：{last_detail}")

        ready, detail = _directml_runtime_probe(staging_python)
        if not ready:
            raise RuntimeError(detail)
        marker = {
            "runtime": "onnxruntime-directml",
            "version": directml_config.DIRECTML_RUNTIME_VERSION,
            "abi": directml_config.DIRECTML_RUNTIME_ABI,
            "python_executable": str(python_exe),
        }
        (staging_root / directml_config.DIRECTML_RUNTIME_MARKER_NAME).write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _rmtree_if_exists(target_root, ignore_errors=True)
        os.replace(staging_root, target_root)
        if not directml_config.is_directml_runtime_ready():
            raise RuntimeError("DirectML 环境安装后完整性检查失败")
        print(f"  DirectML 混合推理环境已安装: {target_root}")
        return True
    except Exception as exc:
        _print_warn(f"  DirectML 混合推理环境安装失败: {exc}")
        return False
    finally:
        _rmtree_if_exists(staging_root, ignore_errors=True)


def _configured_cuda_bundle_urls() -> tuple[str, ...]:
    """Return release URLs, with an explicit local override for QA builds."""
    override = str(os.environ.get("FSV_CUDA_RUNTIME_BUNDLE_URLS", "") or "").strip()
    if override:
        values = [item.strip() for item in override.replace(";", "\n").splitlines()]
        return tuple(dict.fromkeys(item for item in values if item))
    return tuple(dict.fromkeys(directml_config.CUDA_RUNTIME_BUNDLE_URLS))


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _parse_probe_payload(output: str) -> dict | None:
    """Find the JSON payload after native libraries print diagnostics."""
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


def _write_cuda_runtime_marker(
    root: Path,
    python_executable,
    *,
    source: str,
    bundle_manifest: dict | None = None,
    archive_sha256: str = "",
) -> None:
    marker = {
        "runtime": "onnxruntime-gpu",
        "version": directml_config.CUDA_RUNTIME_VERSION,
        "abi": directml_config.CUDA_RUNTIME_ABI,
        "provider": "CUDAExecutionProvider",
        "python_executable": str(python_executable),
    }
    if source == "bundle":
        marker.update(
            {
                "source": "bundle",
                "bundle_format": directml_config.CUDA_RUNTIME_BUNDLE_FORMAT,
                "bundle_version": directml_config.CUDA_RUNTIME_BUNDLE_FORMAT_VERSION,
                "bundle_id": str((bundle_manifest or {}).get("bundle_id") or ""),
                "archive_sha256": archive_sha256,
                "dll_directory": directml_config.CUDA_RUNTIME_BUNDLE_DLL_DIRECTORY,
                "required_dlls": list(directml_config.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS),
            }
        )
    Path(root, directml_config.CUDA_RUNTIME_MARKER_NAME).write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _replace_cuda_runtime_root(staging_root: Path, target_root: Path, *, validator=None) -> None:
    """Atomically activate a prepared runtime while retaining rollback data."""
    backup = target_root.with_name(f".{target_root.name}.previous-{uuid.uuid4().hex}")
    moved_old = False
    try:
        if target_root.exists():
            target_root.replace(backup)
            moved_old = True
        staging_root.replace(target_root)
        if validator is not None and not validator():
            raise RuntimeError("activated CUDA runtime failed its final integrity check")
    except Exception:
        if target_root.exists():
            _rmtree_if_exists(target_root, ignore_errors=True)
        if moved_old and backup.exists():
            backup.replace(target_root)
        raise
    finally:
        if backup.exists():
            _rmtree_if_exists(backup, ignore_errors=True)


def _strip_bundle_venv_bootstrap_files(runtime_root: Path) -> None:
    """Drop pip/setuptools copied by ``venv``; the worker never invokes pip."""
    site_packages = Path(runtime_root) / "Lib" / "site-packages"
    for pattern in ("pip", "pip-*", "setuptools", "setuptools-*", "pkg_resources"):
        for path in site_packages.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    path.unlink()
                except OSError:
                    pass
    scripts = Path(runtime_root) / "Scripts"
    for path in scripts.glob("pip*"):
        try:
            path.unlink()
        except OSError:
            pass


def _valid_probe_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as stream:
            frames = stream.getnframes()
            sample_rate = stream.getframerate()
            channels = stream.getnchannels()
            sample_width = stream.getsampwidth()
            data = stream.readframes(min(frames, sample_rate))
    except (OSError, EOFError, wave.Error):
        return False
    return (
        frames > 0
        and sample_rate == 32000
        and channels == 1
        and sample_width == 2
        and any(data)
    )


def _cuda_voice_package_probe(runtime_python: Path, package_root: Path) -> tuple[bool, str]:
    """Create every current model Session and synthesize Chinese and English."""
    package = Path(package_root)
    if not (package / "manifest.json").is_file() or not (package / "infer.py").is_file():
        return False, "语音包不完整，无法执行 CUDA 真实模型探测"
    try:
        from lib.script.gsvmove.hybrid_worker import VoiceWorkerRuntime

        with tempfile.TemporaryDirectory(prefix="aemeath-cuda-probe-") as tempdir:
            output_root = Path(tempdir)
            runtime = VoiceWorkerRuntime(
                package,
                output_root,
                provider="cuda",
                python_path=Path(runtime_python),
                isolate_user_site=True,
            )
            try:
                cases = (
                    ("zh", "你好。"),
                    ("en", "Hello."),
                )
                for index, (language, text) in enumerate(cases):
                    destination = output_root / f"probe-{index}.wav"
                    runtime.synthesize_to_file(
                        {
                            "text": text,
                            "text_lang": language,
                            "max_steps": 64,
                            "seed": 1,
                        },
                        destination,
                    )
                    if not _valid_probe_wav(destination):
                        return False, f"CUDA {language} 探测没有生成有效 PCM WAV"
            finally:
                runtime.close()
    except Exception as exc:
        detail = str(exc).strip() or repr(exc)
        return False, detail
    return True, ""


def _install_cuda_runtime_bundle(
    python_exe,
    target_root: Path,
    *,
    urls: tuple[str, ...] | None = None,
    voice_package_root: Path | None = None,
) -> tuple[bool, str]:
    """Download, validate and install the pinned CUDA runtime bundle."""
    expected_hash = str(directml_config.CUDA_RUNTIME_BUNDLE_SHA256 or "").strip().lower()
    if len(expected_hash) != 64:
        return False, "CUDA Runtime bundle 尚未配置发布校验值"
    urls = tuple(urls or _configured_cuda_bundle_urls())
    if not urls:
        return False, "CUDA Runtime bundle 没有可用下载源"

    target_root = Path(target_root)
    target_root.parent.mkdir(parents=True, exist_ok=True)
    required_free_bytes = (
        directml_config.CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES
        + directml_config.CUDA_RUNTIME_BUNDLE_PAYLOAD_BYTES
        + directml_config.CUDA_RUNTIME_BUNDLE_STAGING_OVERHEAD_BYTES
    )
    free_bytes = shutil.disk_usage(target_root.parent).free
    if free_bytes < required_free_bytes:
        return False, (
            "CUDA Runtime bundle 临时空间不足："
            f"需要 {required_free_bytes} 字节，可用 {free_bytes} 字节"
        )
    staging_root = target_root.with_name(f".{target_root.name}.bundle-{uuid.uuid4().hex}")
    _rmtree_if_exists(staging_root, ignore_errors=True)
    last_detail = "没有可用的 bundle 下载源"
    try:
        for index, url in enumerate(urls, start=1):
            attempt_root = staging_root.with_name(f"{staging_root.name}-{index}")
            _rmtree_if_exists(attempt_root, ignore_errors=True)
            archive_path = attempt_root / "runtime.zip.part"
            extract_root = attempt_root / "bundle"
            try:
                attempt_root.mkdir(parents=True, exist_ok=True)
                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme not in {"https", "http", "file"}:
                    raise CudaBundleError(f"不支持的 bundle URL 协议：{parsed.scheme}")
                print(
                    f"  下载 CUDA Runtime bundle [{index}/{len(urls)}]: "
                    f"{parsed.hostname or url}"
                )
                _stream_download_with_progress(
                    url,
                    archive_path,
                    label="CUDA Runtime bundle",
                    timeout=90,
                )
                actual_hash = _sha256_path(archive_path)
                if actual_hash != expected_hash:
                    raise CudaBundleError(
                        f"bundle SHA-256 不匹配：{actual_hash} != {expected_hash}"
                    )
                safe_extract_zip(archive_path, extract_root)
                bundle_manifest = validate_bundle_tree(extract_root)

                created = _run(
                    [python_exe, "-m", "venv", "--system-site-packages", str(staging_root)],
                    timeout=180,
                )
                if created is None or created.returncode != 0:
                    detail = _summarize_pip_failure(created.stdout if created is not None else "")
                    raise CudaBundleError(f"创建 CUDA bundle venv 失败：{detail}")
                staging_python = staging_root / "Scripts" / "python.exe"
                payload_root = extract_root / directml_config.CUDA_RUNTIME_BUNDLE_PAYLOAD_ROOT
                payload_site_packages = payload_root / "Lib" / "site-packages"
                staging_site_packages = staging_root / "Lib" / "site-packages"
                if not payload_site_packages.is_dir():
                    raise CudaBundleError("bundle payload 缺少 Lib/site-packages")
                _rmtree_if_exists(staging_site_packages, ignore_errors=True)
                staging_site_packages.parent.mkdir(parents=True, exist_ok=True)
                payload_site_packages.replace(staging_site_packages)
                shutil.copy2(extract_root / "bundle.json", staging_root / "bundle.json")
                shutil.copy2(extract_root / "SHA256SUMS.txt", staging_root / "SHA256SUMS.txt")
                if (extract_root / "THIRD_PARTY_NOTICES.txt").is_file():
                    shutil.copy2(
                        extract_root / "THIRD_PARTY_NOTICES.txt",
                        staging_root / "THIRD_PARTY_NOTICES.txt",
                    )
                _strip_bundle_venv_bootstrap_files(staging_root)
                _write_cuda_runtime_marker(
                    staging_root,
                    python_exe,
                    source="bundle",
                    bundle_manifest=bundle_manifest,
                    archive_sha256=actual_hash,
                )
                ready, detail = _cuda_runtime_probe(staging_python)
                if not ready:
                    raise CudaBundleError(f"bundle CUDA 探测失败：{detail}")
                if voice_package_root is not None:
                    print("  使用当前 ONNX 语音包验证 CUDA 中英文真实推理...")
                    ready, detail = _cuda_voice_package_probe(
                        staging_python,
                        Path(voice_package_root),
                    )
                    if not ready:
                        raise CudaBundleError(f"bundle 真实语音探测失败：{detail}")
                _replace_cuda_runtime_root(
                    staging_root,
                    target_root,
                    validator=directml_config.is_cuda_runtime_ready,
                )
                print(f"  NVIDIA CUDA 精简运行时已安装: {target_root}")
                return True, ""
            except (CudaBundleError, OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
                last_detail = f"{url}: {exc}"
                _print_warn(f"  CUDA Runtime bundle 下载/校验失败：{exc}")
            finally:
                if attempt_root != staging_root:
                    _rmtree_if_exists(attempt_root, ignore_errors=True)
                # A failed venv creation may have used the shared staging root.
                if not target_root.exists() and staging_root.exists():
                    _rmtree_if_exists(staging_root, ignore_errors=True)
        return False, last_detail
    finally:
        _rmtree_if_exists(staging_root, ignore_errors=True)


def _cuda_runtime_probe(runtime_python: Path) -> tuple[bool, str]:
    code = """
import json
import struct
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort

runtime_root = Path(sys.executable).resolve().parent.parent
marker_payload = {}
try:
    marker_payload = json.loads(
        (runtime_root / "runtime.json").read_text(encoding="utf-8")
    )
except Exception:
    marker_payload = {}
relative_dll = str(marker_payload.get("dll_directory") or "").replace("\\\\", "/")
dll_dir = None
if relative_dll:
    candidate = (runtime_root / Path(*relative_dll.split("/"))).resolve()
    try:
        candidate.relative_to(runtime_root)
    except ValueError:
        candidate = None
    if candidate is not None and candidate.is_dir():
        dll_dir = str(candidate)
preload = getattr(ort, "preload_dlls", None)
if callable(preload):
    if dll_dir:
        preload(directory=dll_dir)
    else:
        preload()
from onnx import helper, TensorProto
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
payload = {
    "python": list(sys.version_info[:2]),
    "bits": struct.calcsize("P") * 8,
    "version": ort.__version__,
    "providers": session.get_providers(),
}
print(json.dumps(payload))
"""
    result = _run([str(runtime_python), "-c", code], timeout=60)
    if result is None or result.returncode != 0:
        detail = _summarize_pip_failure(
            "\n".join(
                value
                for value in (
                    result.stdout if result is not None else "",
                    result.stderr if result is not None else "",
                )
                if value
            )
        )
        return False, detail
    payload = _parse_probe_payload(result.stdout or "")
    if payload is None:
        return False, "CUDA 环境探测结果无法解析"
    if payload.get("python") != [3, 11] or payload.get("bits") != 64:
        return False, "CUDA Worker 仅支持 64 位 Python 3.11"
    if payload.get("version") != directml_config.CUDA_RUNTIME_VERSION:
        return False, f"CUDA 运行库版本不匹配：{payload.get('version')}"
    if "CUDAExecutionProvider" not in set(payload.get("providers") or ()):
        diagnostic = _summarize_pip_failure(result.stderr or "")
        suffix = f"；诊断：{diagnostic}" if diagnostic != "pip 未返回错误详情" else ""
        return False, f"CUDAExecutionProvider 不可用：{payload.get('providers')}{suffix}"
    return True, ""

def ensure_cuda_voice_runtime(python_exe, mirrors=None) -> bool:
    """Compatibility helper that installs only the pinned compact Bundle."""
    print("\n  准备 NVIDIA CUDA ONNX 语音运行时...")
    version = _get_version(python_exe)
    architecture = _run(
        [python_exe, "-c", "import struct; print(struct.calcsize('P') * 8)"],
        timeout=30,
    )
    if (
        version[:2] != TARGET_PYTHON
        or architecture is None
        or architecture.returncode != 0
        or (architecture.stdout or "").strip() != "64"
    ):
        _print_warn("  CUDA Worker 仅支持 64 位 Python 3.11，已跳过")
        return False

    target_root = directml_config.get_cuda_runtime_root()
    runtime_python = directml_config.get_cuda_python_path()
    if directml_config.is_cuda_runtime_ready():
        ready, detail = _cuda_runtime_probe(runtime_python)
        if ready:
            print(f"  NVIDIA CUDA 语音运行时已存在: {target_root}")
            return True
        _print_warn(f"  现有 CUDA 环境无效，将重新安装：{detail}")

    voice_package_root = directml_config.get_shared_root_dir() / "voice" / "ONNX_aimisiV2"
    if not (
        (voice_package_root / "manifest.json").is_file()
        and (voice_package_root / "infer.py").is_file()
    ):
        voice_package_root = None
    bundle_ready, bundle_detail = _install_cuda_runtime_bundle(
        python_exe,
        target_root,
        voice_package_root=voice_package_root,
    )
    if bundle_ready:
        return True
    _print_warn(f"  NVIDIA CUDA 精简运行时安装失败: {bundle_detail}")
    return False

__all__ = (
    '_directml_runtime_probe',
    '_bundled_directml_wheels',
    '_directml_pip_command',
    'ensure_directml_hybrid_runtime',
    '_configured_cuda_bundle_urls',
    '_parse_probe_payload',
    '_cuda_voice_package_probe',
    '_install_cuda_runtime_bundle',
    '_cuda_runtime_probe',
    'ensure_cuda_voice_runtime',
)
