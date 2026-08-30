"""Optional CUDA and DirectML voice runtime installation."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from lib.core import voice_runtime_contract as directml_config

from .bootstrap import _get_version, _run
from .catalog import DSH_RUNTIME_INSTALL_TIMEOUT, PYPI_MIRRORS, TARGET_PYTHON
from .console import _fmt_color, _print_warn
from .dependencies import _summarize_pip_failure
from .progress import _run_command_with_progress
from .resources import _rmtree_if_exists


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
    try:
        payload = json.loads((result.stdout or "").strip())
    except (TypeError, ValueError):
        return False, "DirectML 环境探测结果无法解析"
    if payload.get("python") != [3, 11] or payload.get("bits") != 64:
        return False, "DirectML Worker 仅支持 64 位 Python 3.11"
    if payload.get("version") != directml_config.DIRECTML_RUNTIME_VERSION:
        return False, f"DirectML 运行库版本不匹配：{payload.get('version')}"
    if "DmlExecutionProvider" not in set(payload.get("providers") or ()):
        return False, f"DmlExecutionProvider 不可用：{payload.get('providers')}"
    return True, ""

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
        last_detail = "没有可用的 pip 镜像"
        installed = False
        for mirror in sources:
            command = [
                str(staging_python),
                "-m",
                "pip",
                "install",
                directml_config.DIRECTML_RUNTIME_REQUIREMENT,
                "--no-deps",
                "--disable-pip-version-check",
                "--progress-bar",
                "off",
                "-i",
                mirror["url"],
                "--trusted-host",
                mirror["host"],
            ]
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

def _cuda_runtime_probe(runtime_python: Path) -> tuple[bool, str]:
    code = (
        "import json, struct, sys, numpy as np, onnx, onnxruntime as ort; "
        "preload=getattr(ort, 'preload_dlls', None); "
        "preload() if preload else None; "
        "from onnx import helper, TensorProto; "
        "node=helper.make_node('Identity', ['x'], ['y']); "
        "graph=helper.make_graph([node], 'cuda_probe', "
        "[helper.make_tensor_value_info('x', TensorProto.FLOAT, [1])], "
        "[helper.make_tensor_value_info('y', TensorProto.FLOAT, [1])]); "
        "model=helper.make_model(graph, opset_imports=[helper.make_opsetid('', 13)]); "
        "model.ir_version=10; "
        "session=ort.InferenceSession(model.SerializeToString(), "
        "providers=['CUDAExecutionProvider', 'CPUExecutionProvider']); "
        "session.run(None, {'x': np.ones((1,), dtype=np.float32)}); "
        "payload={'python': list(sys.version_info[:2]), "
        "'bits': struct.calcsize('P') * 8, 'version': ort.__version__, "
        "'providers': session.get_providers()}; "
        "print(json.dumps(payload))"
    )
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
    try:
        payload = json.loads((result.stdout or "").strip())
    except (TypeError, ValueError):
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

def ensure_cuda_voice_runtime(python_exe, mirrors) -> bool:
    """Install a self-contained CUDA ONNX worker with mirror fallback."""
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
        last_detail = "没有可用的 pip 镜像"
        installed = False
        for mirror in sources:
            command = [
                str(staging_python),
                "-m",
                "pip",
                "install",
                *directml_config.CUDA_RUNTIME_DEPENDENCIES,
                "--disable-pip-version-check",
                "--progress-bar",
                "off",
                "-i",
                mirror["url"],
                "--trusted-host",
                mirror["host"],
            ]
            return_code, output = _run_command_with_progress(
                command,
                label=f"CUDA pip（{mirror['name']}）",
                kind="pip",
                timeout=DSH_RUNTIME_INSTALL_TIMEOUT,
            )
            if return_code == 0:
                installed = True
                break
            last_detail = (
                f"{mirror['name']}："
                f"{_summarize_pip_failure(output)}"
            )
        if not installed:
            raise RuntimeError(
                f"安装 {directml_config.CUDA_RUNTIME_REQUIREMENT} 及 CUDA 依赖失败：{last_detail}"
            )

        ready, detail = _cuda_runtime_probe(staging_python)
        if not ready:
            raise RuntimeError(detail)
        marker = {
            "runtime": "onnxruntime-gpu",
            "version": directml_config.CUDA_RUNTIME_VERSION,
            "abi": directml_config.CUDA_RUNTIME_ABI,
            "provider": "CUDAExecutionProvider",
            "python_executable": str(python_exe),
        }
        (staging_root / directml_config.CUDA_RUNTIME_MARKER_NAME).write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _rmtree_if_exists(target_root, ignore_errors=True)
        os.replace(staging_root, target_root)
        if not directml_config.is_cuda_runtime_ready():
            raise RuntimeError("CUDA 环境安装后完整性检查失败")
        print(f"  NVIDIA CUDA 语音运行时已安装: {target_root}")
        return True
    except Exception as exc:
        _print_warn(f"  NVIDIA CUDA 语音运行时安装失败: {exc}")
        return False
    finally:
        _rmtree_if_exists(staging_root, ignore_errors=True)

def _has_nvidia_gpu() -> bool:
    """Return whether the local NVIDIA driver exposes at least one GPU."""
    try:
        executable = shutil.which("nvidia-smi")
        if not executable:
            return False
        result = subprocess.run(
            [executable, "-L"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "gpu" in str(result.stdout or "").lower()

def choose_voice_gpu_runtimes() -> tuple[bool, bool]:
    """Return whether the optional CUDA and DirectML workers should be prepared."""
    has_nvidia = _has_nvidia_gpu()
    recommended = 4 if has_nvidia else 2
    recommended_label = _fmt_color("[推荐]", "ok")
    print("\n  可选 ONNX 语音加速运行时：")
    print("    1. 仅 CPU（不额外下载）")
    print(f"    2. {recommended_label if recommended == 2 else '      '} 通用 GPU DirectML（AMD / Intel / NVIDIA）")
    if has_nvidia:
        print("    3. NVIDIA CUDA（N卡加速）")
        print(f"    4. {recommended_label} NVIDIA CUDA + DirectML 后备")
    selected = os.environ.get("FSV_VOICE_RUNTIME_CHOICE", "").strip()
    if selected not in {"1", "2", "3", "4"}:
        try:
            choice_range = "1-4" if has_nvidia else "1-2"
            selected = input(f"  请选择 [{choice_range}，默认 {recommended}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            selected = ""
    if not selected:
        selected = str(recommended)
    if not has_nvidia and selected not in {"1", "2"}:
        selected = str(recommended)
    if selected == "2":
        return False, True
    if selected == "3":
        return True, False
    if selected == "4":
        return True, True
    return False, False


__all__ = (
    '_directml_runtime_probe',
    'ensure_directml_hybrid_runtime',
    '_cuda_runtime_probe',
    'ensure_cuda_voice_runtime',
    '_has_nvidia_gpu',
    'choose_voice_gpu_runtimes',
)
