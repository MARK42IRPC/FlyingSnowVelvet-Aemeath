"""Optional DirectML voice runtime installation."""

import json
import os
from pathlib import Path

from lib.core import voice_runtime_contract as directml_config

from .bootstrap import _get_version, _run
from .catalog import PROJECT_ROOT, PYPI_MIRRORS, TARGET_PYTHON
from .console import _print_warn
from .dependencies import _summarize_pip_failure
from .resources import _rmtree_if_exists


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


__all__ = (
    '_directml_runtime_probe',
    '_bundled_directml_wheels',
    '_directml_pip_command',
    'ensure_directml_hybrid_runtime',
)
