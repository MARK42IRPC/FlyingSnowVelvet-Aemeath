# -*- coding: utf-8 -*-
"""Flying Snow Velvet LTS - Install dependencies and launch.

流程:
1. 扫描系统 Python, 选择可用且版本最优的解释器.
2. 若缺少 pip, 自动尝试安装.
3. 评估镜像延迟并按优先级安装依赖.
4. 写入 py.ini:
   - python_executable
   - pythonw_executable
5. 创建隔离的 DirectML 混合推理环境.
6. 按 resc.net.txt 下载缺失的 Vosk 和动画资源.
7. 准备办公 DSH 侧车源码和固定依赖.
8. 启动主程序.
"""

import configparser
import glob
import hashlib
import json
import os
import re
import queue
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from lib.core import dsh_runtime_contract as dsh_config
from lib.core import voice_runtime_contract as directml_config

PROJECT_ROOT = Path(__file__).parent
RESOURCE_LINKS_FILE = PROJECT_ROOT / "resc.net.txt"
RESOURCE_SOURCE_HOSTS = {
    "gitee.com": "Gitee",
    "github.com": "GitHub",
}
RESOURCE_PING_ATTEMPTS = 3
RESOURCE_PING_TIMEOUT_SECONDS = 5.0
_RESOURCE_SOURCE_ORDER: tuple[str, ...] | None = None
_NODE_SOURCE_ORDER: tuple[str, ...] | None = None

# 最低支持 Python 版本
MIN_VERSION = (3, 7, 0)
TARGET_PYTHON = (3, 11)

PYPI_MIRRORS = [
    {"name": "Tsinghua", "url": "https://pypi.tuna.tsinghua.edu.cn/simple", "host": "pypi.tuna.tsinghua.edu.cn"},
    {"name": "Aliyun", "url": "https://mirrors.aliyun.com/pypi/simple", "host": "mirrors.aliyun.com"},
    {"name": "Tencent", "url": "https://mirrors.cloud.tencent.com/pypi/simple", "host": "mirrors.cloud.tencent.com"},
    {"name": "Douban", "url": "https://pypi.douban.com/simple", "host": "pypi.douban.com"},
    {"name": "Huawei", "url": "https://repo.huaweicloud.com/repository/pypi/simple", "host": "repo.huaweicloud.com"},
    {"name": "USTC", "url": "https://pypi.mirrors.ustc.edu.cn/simple", "host": "pypi.mirrors.ustc.edu.cn"},
    {"name": "PyPI", "url": "https://pypi.org/simple", "host": "pypi.org"},
]

DEPENDENCIES = [
    # (pip package, description, import checks)
    ("PyQt5", "Qt GUI framework", ("PyQt5",)),
    ("Pillow", "image processing", ("PIL",)),
    ("fastapi", "local web relay API framework", ("fastapi",)),
    ("httpx", "async HTTP client for local web relay", ("httpx",)),
    ("packaging", "version / requirement parsing helpers", ("packaging",)),
    ("openai", "OpenAI-compatible client for local web relay", ("openai",)),
    ("opencv-python", "image preprocessing for local web relay", ("cv2",)),
    ("playwright", "browser automation for web login capture", ("playwright",)),
    ("pydantic", "data validation for local web relay", ("pydantic",)),
    ("pydantic-settings", "settings loader for local web relay", ("pydantic_settings",)),
    ("requests", "HTTP client", ("requests",)),
    ("qrcode", "QR code generation for music login", ("qrcode",)),
    ("sse-starlette", "SSE streaming for local web relay", ("sse_starlette",)),
    ("mutagen", "local audio metadata parsing", ("mutagen",)),
    ("jieba-fast", "compiled Chinese tokenizer for genie-tts", ("jieba_fast",)),
    ("opencc-python-reimplemented", "Chinese script conversion for the ONNX text frontend", ("opencc",)),
    ("genie-tts", "bilingual ONNX text frontend", ("spec:genie_tts",)),
    ("numpy", "numerical runtime for ONNX voice synthesis", ("numpy",)),
    ("onnx", "ONNX model loader for voice synthesis", ("onnx",)),
    ("onnxruntime", "lightweight ONNX voice inference runtime", ("onnxruntime",)),
    ("rarfile", "safe multi-volume RAR parser", ("rarfile",)),
    ("soundfile", "ONNX voice audio writer", ("soundfile",)),
    ("soxr", "ONNX voice audio resampler", ("soxr",)),
    ("pycaw", "Windows audio meter", ("pycaw",)),
    ("comtypes", "COM bindings for pycaw", ("comtypes",)),
    ("pywin32", "Windows COM bridge (win32com/pythoncom)", ("pythoncom", "win32com")),
    ("sounddevice", "microphone capture for speech-to-text", ("sounddevice",)),
    ("webrtcvad-wheels", "lightweight speech endpoint detection", ("webrtcvad",)),
    ("uvicorn", "ASGI server for local web relay", ("uvicorn",)),
    ("vosk", "offline speech-to-text engine", ("vosk",)),
]

TOTAL_STEPS = 10

def _enable_ansi_color() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    if any(key in os.environ for key in ("ANSICON", "WT_SESSION", "TERM_PROGRAM")):
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            if mode.value & 0x0004:  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                return True
            if kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                return True
    except Exception:
        pass
    return False


_COLOR_ENABLED = _enable_ansi_color()
_COLOR_RESET = "\033[0m"
_COLOR_MAP = {
    "stage": "\033[95m",
    "info": "\033[96m",
    "ok": "\033[92m",
    "warn": "\033[93m",
    "error": "\033[91m",
    "progress_current": "\033[96m",
    "progress_overall": "\033[95m",
    "progress_track": "\033[90m",
    "progress_value": "\033[97m",
}
_LABELS = {
    "info": "[信息] ",
    "ok": "[完成] ",
    "warn": "[警告] ",
    "error": "[错误] ",
}


def _fmt_color(text: str, kind: str) -> str:
    if not _COLOR_ENABLED:
        return text
    code = _COLOR_MAP.get(kind)
    if not code:
        return text
    return f"{code}{text}{_COLOR_RESET}"


def _print_kind(text: str, kind: str = "info", *, prefix: bool = True) -> None:
    if prefix:
        text = f"{_LABELS.get(kind, '')}{text}"
    print(_fmt_color(text, kind))


def _print_info(text: str) -> None:
    _print_kind(text, "info")


def _print_warn(text: str) -> None:
    _print_kind(text, "warn")


def _print_error(text: str) -> None:
    _print_kind(text, "error")


def _print_stage(step: int, text: str) -> None:
    message = f"\n[{step}/{TOTAL_STEPS}] {text}"
    print(_fmt_color(message, "stage"))


def _console_safe(text: str) -> str:
    if os.name == "nt" and text:
        macro = _to_env_macro_path(text)
        try:
            macro.encode("ascii")
            text = macro
        except Exception:
            text = _to_batch_safe_path(text)

    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except Exception:
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


VOSK_MODEL_MARKERS = ("am", "conf", "graph", "ivector")
VOSK_MODELS_DIR = PROJECT_ROOT / "resc" / "models"
VOSK_MODEL_SPECS = (
    {
        "name": "vosk-model-small-cn-0.22",
        "label": "Chinese",
        "resource_name": "vosk-model-small-cn-0.22.zip",
    },
    {
        "name": "vosk-model-small-en-us-0.15",
        "label": "English",
        "resource_name": "vosk-model-small-en-us-0.15.zip",
    },
)
SEANIMA_TARGET_DIR = PROJECT_ROOT / "resc" / "GIF" / "SEanima"
SEANIMA_RESOURCE_NAME = "SEanima.zip"
SEANIMA_ARCHIVE = PROJECT_ROOT / "resc" / "GIF" / SEANIMA_RESOURCE_NAME
JIEBA_FAST_PACKAGE = "jieba-fast"
JIEBA_FAST_WHEEL_NAME = "jieba_fast-0.53-cp311-cp311-win_amd64.whl"
JIEBA_FAST_WHEEL_SHA256 = "a5d9cf41d6817963a73f672a429dbfe5b03a4ff327cedf490d5f2b21be8c00d0"
BINARY_ONLY_PACKAGES = frozenset({"opencc-python-reimplemented"})

DSH_RUNTIME_INSTALL_TIMEOUT = 30 * 60
PACKAGE_REQUIREMENTS = {
    "opencc-python-reimplemented": "opencc-python-reimplemented>=0.1.7,<1",
}

_NOT_FOUND_MARKERS = (
    "no matching distribution found",
    "could not find a version that satisfies",
    "no distributions at all",
)


def _run(cmd, timeout=12, *, cwd=None):
    """Run command quietly. Return CompletedProcess or None."""
    try:
        options = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "ignore",
            "timeout": timeout,
        }
        if cwd is not None:
            options["cwd"] = str(cwd)
        return subprocess.run(
            cmd,
            **options,
        )
    except Exception:
        return None


def _python_module_cmd(python_exe, module, *args):
    return [python_exe, "-m", module, *args]


def _run_python_module(python_exe, module, *args, timeout=12):
    return _run(_python_module_cmd(python_exe, module, *args), timeout=timeout)


def _run_pip(python_exe, *args, timeout=12):
    return _run_python_module(python_exe, "pip", *args, timeout=timeout)


_UV_PYVENV_PATTERN = re.compile(r"(?im)^\s*uv\s*=")
_UV_MANAGED_TEXT_PATTERN = re.compile(
    r"(?i)(?:managed\s+by\s+uv|\buv[- ]managed\b)"
)


def _read_environment_marker(path: Path, limit=16 * 1024) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as stream:
            return stream.read(limit)
    except OSError:
        return ""


def _python_environment_roots(python_exe) -> tuple[Path, ...]:
    value = str(python_exe or "").strip().strip('"')
    if not value:
        return ()

    try:
        lexical_path = Path(os.path.abspath(os.path.expandvars(os.path.expanduser(value))))
    except (OSError, TypeError, ValueError):
        return ()

    executable_paths = [lexical_path]
    try:
        resolved_path = lexical_path.resolve(strict=False)
    except OSError:
        resolved_path = lexical_path
    if os.path.normcase(str(resolved_path)) != os.path.normcase(str(lexical_path)):
        executable_paths.append(resolved_path)

    roots = []
    seen = set()
    for executable_path in executable_paths:
        parent = executable_path.parent
        root = parent.parent if parent.name.casefold() in {"scripts", "bin"} else parent
        key = os.path.normcase(os.path.abspath(str(root)))
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return tuple(roots)


def _is_uv_managed_python(python_exe) -> bool:
    """Return whether an interpreter belongs to a locked uv-managed environment."""
    for root in _python_environment_roots(python_exe):
        pyvenv_text = _read_environment_marker(root / "pyvenv.cfg")
        if pyvenv_text and _UV_PYVENV_PATTERN.search(pyvenv_text):
            return True

        managed_markers = [root / "Lib" / "EXTERNALLY-MANAGED"]
        lib_root = root / "lib"
        if lib_root.is_dir():
            managed_markers.extend(lib_root.glob("python*/EXTERNALLY-MANAGED"))
        for marker in managed_markers:
            marker_text = _read_environment_marker(marker)
            if marker_text and _UV_MANAGED_TEXT_PATTERN.search(marker_text):
                return True
    return False


def _discover_all_pythons():
    """Find python executables from current runtime, launcher, PATH, registry and common paths."""
    import glob

    candidates = []

    current_exe = sys.executable or ""
    if current_exe and os.path.isfile(current_exe):
        candidates.append(current_exe)

    # 1) py launcher
    r = _run(["py", "-0p"])
    if r and r.returncode == 0:
        for line in r.stdout.splitlines():
            m = re.search(r"([A-Za-z]:\\.*python(?:w)?\.exe)$", line.strip(), re.IGNORECASE)
            if m:
                exe = m.group(1)
                if os.path.isfile(exe):
                    candidates.append(exe)

    # 2) PATH commands
    for name in ("python", "python3"):
        try:
            r = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f"Get-Command {name} -All -ErrorAction SilentlyContinue | ForEach-Object {{$_.Source}}",
                ]
            )
        except Exception:
            r = None
        if r and r.returncode == 0:
            for line in r.stdout.splitlines():
                exe = line.strip()
                if exe and os.path.isfile(exe):
                    candidates.append(exe)

    # 3) Windows registry
    try:
        import winreg

        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Python\PythonCore"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Python\PythonCore"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Python\PythonCore"),
        ]
        for hive, base in reg_paths:
            try:
                with winreg.OpenKey(hive, base) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            ver = winreg.EnumKey(key, i)
                            with winreg.OpenKey(hive, rf"{base}\{ver}\InstallPath") as ip:
                                try:
                                    exe, _ = winreg.QueryValueEx(ip, "ExecutablePath")
                                    if os.path.isfile(exe):
                                        candidates.append(exe)
                                except OSError:
                                    base_dir, _ = winreg.QueryValueEx(ip, "")
                                    if base_dir:
                                        exe = os.path.join(base_dir, "python.exe")
                                        if os.path.isfile(exe):
                                            candidates.append(exe)
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass

    # 4) App Paths registry entries
    try:
        import winreg

        app_path_roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python.exe"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python3.exe"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python3.exe"),
        ]
        for hive, key_path in app_path_roots:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    exe, _ = winreg.QueryValueEx(key, "")
                    if os.path.isfile(exe):
                        candidates.append(exe)
            except OSError:
                pass
    except ImportError:
        pass

    # 5) common install paths
    local_py = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python")
    home = os.path.expanduser("~")
    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    patterns = [
        os.path.join(local_py, "Python3*", "python.exe"),
        os.path.join(home, "scoop", "apps", "python*", "current", "python.exe"),
        os.path.join(program_data, "chocolatey", "lib", "python*", "tools", "python.exe"),
        r"C:\Python3*\python.exe",
        r"C:\Program Files\Python3*\python.exe",
        r"C:\Program Files (x86)\Python3*\python.exe",
        os.path.join(home, "miniconda3", "python.exe"),
        os.path.join(home, "anaconda3", "python.exe"),
        os.path.join(home, "miniconda3", "envs", "*", "python.exe"),
        os.path.join(home, "anaconda3", "envs", "*", "python.exe"),
    ]
    for pat in patterns:
        for exe in glob.glob(pat):
            if os.path.isfile(exe):
                candidates.append(exe)

    # deduplicate
    seen = set()
    unique = []
    for exe in candidates:
        key = os.path.normcase(os.path.abspath(exe))
        if key not in seen and not _is_uv_managed_python(exe):
            seen.add(key)
            unique.append(exe)
    return unique


def _probe_python_info(python_exe):
    """Return resolved executable path and version tuple for a candidate."""
    code = (
        "import json, sys; "
        "payload = {'version': list(sys.version_info[:3]), 'executable': sys.executable}; "
        "sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))"
    )
    r = _run([python_exe, "-c", code])
    if not r or r.returncode != 0:
        return None

    try:
        payload = json.loads((r.stdout or "").strip())
    except Exception:
        return None

    version_parts = payload.get("version")
    resolved_exe = str(payload.get("executable") or "").strip()
    if not resolved_exe or "WindowsApps" in resolved_exe:
        return None

    try:
        parts = list(version_parts or [])[:3]
        while len(parts) < 3:
            parts.append(0)
        version = tuple(int(x) for x in parts)
    except Exception:
        return None

    return resolved_exe, version


def _current_runtime_executable():
    probed = _probe_python_info(sys.executable)
    if probed:
        return probed[0]
    return sys.executable


def _get_version(python_exe):
    """Return (major, minor, patch), or (0,0,0) if unknown."""
    probed = _probe_python_info(python_exe)
    if not probed:
        return (0, 0, 0)
    _resolved_exe, version = probed
    return version


def _has_pip(python_exe):
    r = _run_pip(python_exe, "--version")
    return r is not None and r.returncode == 0


def _fmt_ver(ver):
    return ".".join(str(v) for v in ver)


def _sort_key(item):
    """Prefer Python 3.11 for the published native dependency wheels."""
    ver, exe = item
    current_exe = _current_runtime_executable()
    current = 0 if os.path.normcase(os.path.abspath(exe)) == os.path.normcase(os.path.abspath(current_exe)) else 1
    target_major, target_minor = TARGET_PYTHON
    exact_target = 0 if (ver[0], ver[1]) == TARGET_PYTHON else 1
    distance = abs(ver[1] - target_minor) if ver[0] == target_major else 99
    non_py3 = 0 if ver[0] == target_major else 1
    return (exact_target, current, distance, non_py3, -ver[0], -ver[1], -ver[2], exe.lower())


def _fallback_python_selection(message="  No Python found via scan, fallback to current interpreter"):
    if _is_uv_managed_python(sys.executable):
        raise RuntimeError(
            "未找到可用的非 uv 管理 Python；请安装独立的 64 位 Python 3.11"
        )
    print(message)
    return sys.executable, _has_pip(sys.executable)


def _select_ranked_python(candidates, *, pip_ready):
    if not candidates:
        return None
    candidates.sort(key=_sort_key)
    best_ver, best_exe = candidates[0]
    detail = "pip ready" if pip_ready else "pip will be installed"
    print(f"\n  -> Selected Python {_fmt_ver(best_ver)} ({detail})")
    print(f"     Path: {_console_safe(best_exe)}")
    return best_exe, pip_ready


def select_best_python():
    _print_stage(1, "扫描可用的 Python 解释器...")

    all_exes = _discover_all_pythons()
    if not all_exes:
        return _fallback_python_selection()

    with_pip = []
    without_pip = []

    for exe in all_exes:
        if _is_uv_managed_python(exe):
            print(f"  [skip] uv-managed Python: {_console_safe(exe)}")
            continue
        probed = _probe_python_info(exe)
        if not probed:
            print(f"  [skip] Python probe failed: {_console_safe(exe)}")
            continue

        resolved_exe, ver = probed
        if _is_uv_managed_python(resolved_exe):
            print(f"  [skip] uv-managed Python: {_console_safe(resolved_exe)}")
            continue
        if ver < MIN_VERSION:
            print(f"  [skip] Python {_fmt_ver(ver)} below minimum {_fmt_ver(MIN_VERSION)}: {_console_safe(resolved_exe)}")
            continue

        has_pip = _has_pip(resolved_exe)
        status = "pip" if has_pip else "no-pip"
        if os.path.normcase(os.path.abspath(resolved_exe)) == os.path.normcase(os.path.abspath(_current_runtime_executable())):
            pref = "current"
        elif (ver[0], ver[1]) == TARGET_PYTHON:
            pref = "target-3.11"
        elif ver[0] == TARGET_PYTHON[0]:
            pref = f"distance-{abs(ver[1] - TARGET_PYTHON[1])}"
        else:
            pref = "non-target-major"
        print(f"  [{status}] Python {_fmt_ver(ver):<8} {pref:<14} {_console_safe(resolved_exe)}")
        (with_pip if has_pip else without_pip).append((ver, resolved_exe))

    selected = _select_ranked_python(with_pip, pip_ready=True)
    if selected is not None:
        return selected

    selected = _select_ranked_python(without_pip, pip_ready=False)
    if selected is not None:
        return selected

    return _fallback_python_selection("  No executable candidate remained, fallback to current interpreter")


def ensure_pip(python_exe):
    _print_info("\npip 缺失，尝试自动安装...")

    # A) ensurepip
    r = _run_python_module(python_exe, "ensurepip", "--upgrade", timeout=120)
    if r and r.returncode == 0 and _has_pip(python_exe):
        _print_kind("  已通过 ensurepip 安装 pip", "ok", prefix=False)
        return True

    # B) get-pip.py
    _print_kind("  ensurepip 失败，尝试 get-pip.py...", "warn", prefix=False)

    tmp = Path(os.environ.get("TEMP", "C:\\Temp")) / "get-pip.py"
    try:
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", str(tmp))
        r = _run([python_exe, str(tmp)], timeout=240)
        if r and r.returncode == 0 and _has_pip(python_exe):
            _print_kind("  已通过 get-pip.py 安装 pip", "ok", prefix=False)
            return True
    except Exception as e:
        _print_kind(f"  get-pip.py 执行失败: {e}", "warn", prefix=False)
    finally:
        _unlink_if_exists(tmp, ignore_errors=True)

    _print_kind("  自动安装 pip 失败", "error", prefix=False)
    return False


def _resolve_pythonw_path(python_exe, fallback="pythonw"):
    """Infer pythonw.exe from selected python path."""
    try:
        p = Path(python_exe)
        if p.is_file():
            if p.name.lower() == "pythonw.exe":
                return str(p)
            pw = p.with_name("pythonw.exe")
            if pw.exists():
                return str(pw)
    except Exception:
        pass
    return fallback


def _to_short_windows_path(path):
    """Convert path to DOS 8.3 short path for batch-file compatibility."""
    if os.name != "nt" or not path:
        return path

    if path.lower() in {"python", "python3", "pythonw", "py"}:
        return path

    target = os.path.abspath(path)
    if not os.path.exists(target):
        return path

    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(4096)
        size = ctypes.windll.kernel32.GetShortPathNameW(target, buf, len(buf))
        if size:
            return buf.value
    except Exception:
        pass

    return path


def _to_env_macro_path(path):
    """Replace common user/system prefixes with %ENV% form to avoid UTF-8 parsing issues in batch."""
    if os.name != "nt" or not path:
        return path

    candidates = [
        "LOCALAPPDATA",
        "APPDATA",
        "USERPROFILE",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramData",
        "SystemRoot",
    ]

    raw = os.path.abspath(path)
    raw_lower = raw.lower()
    best = None

    for key in candidates:
        val = os.environ.get(key)
        if not val:
            continue
        base = os.path.abspath(val).rstrip("\\/")
        if not base:
            continue
        base_lower = base.lower()
        if raw_lower == base_lower or raw_lower.startswith(base_lower + "\\"):
            if best is None or len(base) > len(best[1]):
                best = (key, base)

    if not best:
        return path

    key, base = best
    suffix = raw[len(base) :]
    suffix = suffix.lstrip("\\/")
    if suffix:
        return f"%{key}%\\{suffix}"
    return f"%{key}%"


def _to_batch_safe_path(path):
    """Prefer ASCII-friendly path when original path contains non-ASCII chars."""
    if not path:
        return path
    if all(ord(ch) < 128 for ch in path):
        return path

    short = _to_short_windows_path(path)
    if all(ord(ch) < 128 for ch in short):
        return short

    macro = _to_env_macro_path(short)
    if all(ord(ch) < 128 for ch in macro):
        return macro

    macro = _to_env_macro_path(path)
    if all(ord(ch) < 128 for ch in macro):
        return macro

    return short


def save_config(python_exe):
    """Write python/pythonw executable paths to py.ini."""
    pythonw_exe = _resolve_pythonw_path(python_exe)
    python_cfg = _to_batch_safe_path(python_exe)
    pythonw_cfg = _to_batch_safe_path(pythonw_exe)
    cfg = configparser.RawConfigParser()
    cfg["Python"] = {
        "python_executable": python_cfg,
        "pythonw_executable": pythonw_cfg,
    }

    try:
        with open(PROJECT_ROOT / "py.ini", "w", encoding="utf-8") as f:
            cfg.write(f)
        print("\n[config] py.ini updated:")
        print(f"  python_executable  = {python_cfg}")
        print(f"  pythonw_executable = {pythonw_cfg}")
    except Exception as e:
        print(f"\n[config] failed to write py.ini: {e}")


def _tcp_ms(host, port=443, timeout=4.0):
    """Return TCP connect latency in milliseconds, or inf if unreachable."""
    try:
        start = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return (time.perf_counter() - start) * 1000
    except Exception:
        return float("inf")


def benchmark_mirrors():
    _print_stage(2, "并发测试依赖镜像延迟...")
    scored = []
    # Probe all mirrors at once so one unavailable host cannot add its timeout
    # to every other mirror. Ordering is restored below for deterministic logs.
    with ThreadPoolExecutor(max_workers=min(8, len(PYPI_MIRRORS))) as executor:
        latencies = list(executor.map(lambda mirror: _tcp_ms(mirror["host"], timeout=2.0), PYPI_MIRRORS))

    for mirror, lat in zip(PYPI_MIRRORS, latencies):
        if lat == float("inf"):
            print(f"  {mirror['name']:<10} unreachable")
        else:
            print(f"  {mirror['name']:<10} {lat:>6.0f} ms")
        scored.append((lat, mirror))

    scored.sort(key=lambda x: x[0])
    reachable = [m for lat, m in scored if lat < float("inf")]
    unreachable = [m for lat, m in scored if lat == float("inf")]

    if reachable:
        best_lat = next(lat for lat, m in scored if m is reachable[0])
        _print_kind(f"\n  -> 最优镜像: {reachable[0]['name']} ({best_lat:.0f} ms)", "ok", prefix=False)
    else:
        _print_warn("\n  -> 所有镜像均不可达，将逐一尝试")

    return reachable + unreachable


_SPEC_ONLY_IMPORT_PREFIX = "spec:"


def _pkg_installed(python_exe, pkg, import_checks=()):
    """
    Check package availability by:
    1) pip metadata exists
    2) referenced runtime modules can be imported
    """
    r = _run_pip(python_exe, "show", pkg)
    if not (r is not None and r.returncode == 0):
        return False

    checks = [str(item or "").strip() for item in import_checks if str(item or "").strip()]
    if not checks:
        return True

    spec_modules = [
        item[len(_SPEC_ONLY_IMPORT_PREFIX):]
        for item in checks
        if item.startswith(_SPEC_ONLY_IMPORT_PREFIX)
    ]
    import_modules = [
        item
        for item in checks
        if not item.startswith(_SPEC_ONLY_IMPORT_PREFIX)
    ]
    statements = ["import importlib.util"] if spec_modules else []
    statements.extend(
        f"assert importlib.util.find_spec({module!r}) is not None"
        for module in spec_modules
    )
    statements.extend(f"import {module}" for module in import_modules)
    code = "; ".join(statements)
    ir = _run([python_exe, "-c", code])
    return ir is not None and ir.returncode == 0


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _render_dependency_bar(current, total, width=26, *, color_kind=None):
    if total <= 0:
        percent = 100
    else:
        percent = max(0, min(100, int(round((current / total) * 100))))
    filled = int(round((percent / 100) * width))
    complete = "━" * filled
    remaining = "─" * (width - filled)
    if not _COLOR_ENABLED or not color_kind:
        return f"[{complete}{remaining}]"
    complete_color = _COLOR_MAP.get(color_kind, _COLOR_MAP["info"])
    track_color = _COLOR_MAP["progress_track"]
    return (
        f"[{complete_color}{complete}{_COLOR_RESET}"
        f"{track_color}{remaining}{_COLOR_RESET}]"
    )


class _DependencyCheckProgressDisplay:
    """One in-place line for the potentially slow dependency availability scan."""

    def __init__(self):
        self._drawn = False
        self._last_payload = None
        self._line_width = 0

    def update(self, package, current, total, *, force=False):
        checked = max(0, min(int(current), max(0, int(total))))
        total = max(0, int(total))
        payload = (str(package), checked, total)
        if payload == self._last_payload and not force:
            return
        self._last_payload = payload
        percent = 100 if total <= 0 else int(round((checked / total) * 100))
        label = _fmt_color("正在检查依赖", "info")
        bar = _render_dependency_bar(
            checked,
            total,
            color_kind="progress_current",
        )
        value = _fmt_color(f"{percent:>3}%", "progress_value")
        count = _fmt_color(f"{checked}/{total}", "progress_value")
        package_name = _fmt_color(str(package), "progress_current")
        line = f"  {label} {bar} {value}  {count}  {package_name}"
        self._line_width = max(self._line_width, len(line))
        if _COLOR_ENABLED:
            sys.stdout.write(f"\r\033[2K{line}")
        else:
            sys.stdout.write("\r" + line)
        sys.stdout.flush()
        self._drawn = True

    def clear(self):
        if not self._drawn:
            return
        if _COLOR_ENABLED:
            sys.stdout.write("\r\033[2K")
        else:
            sys.stdout.write("\r" + " " * self._line_width + "\r")
        sys.stdout.flush()
        self._drawn = False


class _DependencyProgressDisplay:
    def __init__(self):
        self._drawn = False
        self._last_payload = None
        self._active_package = None
        self._active_percent = 0
        self._overall_total = None
        self._overall_current = 0

    def update(
        self,
        package,
        package_percent,
        overall_current,
        overall_total,
        *,
        force=False,
        reset=False,
    ):
        package = str(package)
        percent = max(0, min(100, int(package_percent)))
        total = max(0, int(overall_total))
        if reset or package != self._active_package:
            self._active_package = package
            self._active_percent = 0
        percent = max(self._active_percent, percent)
        self._active_percent = percent
        if self._overall_total != total:
            self._overall_total = total
            self._overall_current = 0
        current = max(0, min(total, int(overall_current)))
        current = max(self._overall_current, current)
        self._overall_current = current
        payload = (package, percent, current, total)
        if payload == self._last_payload and not force:
            return
        self._last_payload = payload
        package_bar = _render_dependency_bar(
            percent,
            100,
            color_kind="progress_current",
        )
        overall_bar = _render_dependency_bar(
            current,
            total,
            color_kind="progress_overall",
        )
        current_label = _fmt_color("当前依赖", "info")
        overall_label = _fmt_color("整体进度", "stage")
        package_value = _fmt_color(f"{percent:>3}%", "progress_value")
        overall_value = _fmt_color(
            f"{current}/{total}",
            "progress_value",
        )
        package_name = _fmt_color(package, "progress_current")
        first = f"  {current_label} {package_bar} {package_value}  {package_name}"
        second = f"  {overall_label} {overall_bar} {overall_value}"

        # The GUI installer consumes explicit UTF-8 progress records. Keep the
        # regular non-interactive console output compact, while allowing the
        # installer to receive every monotonic update instead of only 5%.
        if os.environ.get("FLYING_SNOW_INSTALLER") == "1":
            print(first, flush=True)
            print(second, flush=True)
            self._drawn = True
            return

        if _COLOR_ENABLED:
            if self._drawn:
                sys.stdout.write("\033[2F")
            sys.stdout.write(f"\033[2K{first}\n\033[2K{second}\n")
            sys.stdout.flush()
            self._drawn = True
            return

        if not self._drawn or force:
            print(first)
            print(second)
            self._drawn = True


_PIP_PROGRESS_STAGES = (
    (95, ("successfully installed", "already satisfied")),
    (78, (
        "installing collected",
        "installing build dependencies",
        "building wheel",
        "running setup.py",
        "running bdist_wheel",
    )),
    (48, ("downloading", "using cached", "using cache", "fetching")),
    (22, (
        "collecting",
        "preparing metadata",
        "getting requirements to build wheel",
        "checking if the build backend supports a build_editable",
    )),
)


def _pip_progress_from_output(line, current):
    """Map pip's coarse log phases to a monotonic, honest progress value."""
    text = _ANSI_ESCAPE_PATTERN.sub("", str(line or "")).replace("\r", " ").strip().lower()
    current = max(0, min(100, int(current)))
    if not text:
        return current
    for value, markers in _PIP_PROGRESS_STAGES:
        if any(marker in text for marker in markers):
            return max(current, value)
    return max(current, 5)


class _MonotonicProgressReporter:
    """Keep retries and noisy callbacks from making one job appear to regress."""

    def __init__(self, callback):
        self._callback = callback
        self._value = 0

    @property
    def value(self):
        return self._value

    def __call__(self, value):
        value = max(0, min(100, int(value)))
        if value < self._value:
            value = self._value
        if value == self._value:
            return
        self._value = value
        self._callback(value)


def _run_pip_requirement_with_progress(
    python_exe,
    requirement,
    progress_callback,
    *,
    mirror=None,
    only_binary=None,
):
    command = _python_module_cmd(
        python_exe,
        "pip",
        "install",
        str(requirement),
        "--no-warn-script-location",
        "--disable-pip-version-check",
        "--progress-bar",
        "off",
    )
    if only_binary:
        command.extend(("--only-binary", str(only_binary)))
    if mirror is not None:
        command.extend(
            (
                "-i",
                mirror["url"],
                "--trusted-host",
                mirror["host"],
            )
        )
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        return 127, f"无法启动 pip：{exc}"
    output_queue = queue.Queue()

    def read_output():
        stream = proc.stdout
        if stream is None:
            output_queue.put(None)
            return
        try:
            for line in stream:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True, name="pip-progress-reader")
    reader.start()
    output_tail = []
    percent = 5
    reader_done = False
    progress_callback(percent)
    while proc.poll() is None or not reader_done:
        try:
            line = output_queue.get(timeout=0.12)
        except queue.Empty:
            line = ""
        if line is None:
            reader_done = True
        elif line:
            output_tail.append(line)
            if len(output_tail) > 160:
                del output_tail[:-160]
            next_percent = _pip_progress_from_output(line, percent)
            if next_percent != percent:
                percent = next_percent
                progress_callback(percent)

    if proc.returncode == 0 and percent < 95:
        progress_callback(95)

    reader.join(timeout=1.0)
    return proc.returncode, "".join(output_tail)


class _RuntimeInstallProgress:
    """Render live stage progress using the same bar as dependency installs."""

    def __init__(self, label, *, width=26):
        self._label = str(label)
        self._width = max(10, int(width))
        self._started = time.monotonic()
        self._percent = 0
        self._last_detail = ""
        self._last_percent = -1
        self._last_draw = 0.0
        self._drawn = False
        self._line_width = 0

    @property
    def percent(self):
        return self._percent

    def update(self, percent=None, detail="", *, force=False):
        now = time.monotonic()
        if percent is not None:
            self._percent = max(self._percent, min(100, int(percent)))
        detail = " ".join(str(detail or "").split())
        if not detail:
            detail = "处理中"
        if (
            not force
            and detail == self._last_detail
            and self._percent == self._last_percent
            and now - self._last_draw < 1.0
        ):
            return
        self._last_detail = detail
        self._last_percent = self._percent
        self._last_draw = now
        elapsed = int(max(0, now - self._started))
        minutes, seconds = divmod(elapsed, 60)
        bar = _render_dependency_bar(
            self._percent,
            100,
            width=self._width,
            color_kind="progress_current",
        )
        value = _fmt_color(f"{self._percent:>3}%", "progress_value")
        label = _fmt_color(self._label, "info")
        line = f"  {label} {bar} {value}  {detail}  [{minutes:02d}:{seconds:02d}]"
        self._line_width = max(self._line_width, len(line))
        # The GUI installer consumes a pipe rather than a terminal. Emit one
        # complete UTF-8 record per update so DSH/npm progress is observable
        # immediately instead of being hidden behind carriage-return redraws.
        if os.environ.get("FLYING_SNOW_INSTALLER") == "1":
            print(line, flush=True)
            self._drawn = True
            return
        if _COLOR_ENABLED:
            sys.stdout.write(f"\r\033[2K{line}")
        else:
            sys.stdout.write("\r" + line)
        sys.stdout.flush()
        self._drawn = True

    def finish(self, detail, *, success=False):
        if success:
            self._percent = 100
        if not self._drawn:
            self.update(self._percent, detail, force=True)
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._drawn = False
            return
        self.update(self._percent, detail, force=True)
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._drawn = False


def _runtime_install_stage(line, *, kind):
    """Translate npm/pip output into monotonic stage percentages and labels."""
    text = _ANSI_ESCAPE_PATTERN.sub("", str(line or ""))
    lowered = text.replace("\r", " ").strip().lower()
    if not lowered:
        return None
    if kind == "npm":
        markers = (
            (12, "准备 lockfile", ("ideal tree", "loadideal", "sill ideal")),
            (28, "解析依赖树", ("reify", "place")),
            (48, "下载依赖", ("http fetch", "fetch manifest", "fetch metadata")),
            (72, "安装依赖", ("extract", "tarball", "unpack")),
            (90, "整理 node_modules", ("reify:load", "reify:save")),
            (96, "依赖安装完成", ("added ", "up to date", "audited ")),
        )
    else:
        markers = (
            (96, "依赖安装完成", ("successfully installed", "already satisfied")),
            (76, "构建/安装依赖", ("installing collected", "building wheel", "running setup.py")),
            (48, "下载 CUDA 依赖", ("downloading", "using cached", "using cache", "fetching")),
            (22, "解析 CUDA 依赖", ("collecting", "preparing metadata", "getting requirements")),
        )
    for percent, label, candidates in markers:
        if any(candidate in lowered for candidate in candidates):
            return percent, label
    return None


def _run_command_with_progress(command, *, label, kind, timeout, cwd=None):
    """Run a long installer command while forwarding useful live status."""
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(cwd) if cwd is not None else None,
        )
    except OSError as exc:
        return 127, f"无法启动安装命令：{exc}"

    output_queue = queue.Queue()

    def read_output():
        stream = proc.stdout
        if stream is None:
            output_queue.put(None)
            return
        try:
            for line in stream:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(
        target=read_output,
        daemon=True,
        name="runtime-install-progress-reader",
    )
    reader.start()
    display = _RuntimeInstallProgress(label)
    display.update(5, "启动安装", force=True)
    output_tail = []
    reader_done = False
    deadline = time.monotonic() + max(1, int(timeout))
    while proc.poll() is None or not reader_done:
        if proc.poll() is None and time.monotonic() >= deadline:
            try:
                proc.kill()
            except OSError:
                pass
            display.finish("超时")
            reader_done = True
            return 124, "安装命令超时"
        try:
            line = output_queue.get(timeout=0.25)
        except queue.Empty:
            display.update(detail="处理中")
            continue
        if line is None:
            reader_done = True
            continue
        output_tail.append(line)
        if len(output_tail) > 160:
            del output_tail[:-160]
        stage = _runtime_install_stage(line, kind=kind)
        if stage is not None:
            percent, detail = stage
            display.update(percent, detail)
        else:
            display.update(detail="处理中")
    reader.join(timeout=1.0)
    return_code = proc.returncode
    display.finish("完成" if return_code == 0 else "失败", success=return_code == 0)
    return return_code, "".join(output_tail)


def _run_pip_install_with_progress(python_exe, pkg, mirror, progress_callback):
    return _run_pip_requirement_with_progress(
        python_exe,
        PACKAGE_REQUIREMENTS.get(pkg, pkg),
        progress_callback,
        mirror=mirror,
        only_binary=pkg if pkg in BINARY_ONLY_PACKAGES else None,
    )


def _summarize_pip_failure(output):
    lines = []
    for raw_line in str(output or "").splitlines():
        line = _ANSI_ESCAPE_PATTERN.sub("", raw_line).strip()
        if line:
            lines.append(line)
    if not lines:
        return "pip 未返回错误详情"

    preferred = [
        line
        for line in lines
        if line.lower().startswith(("error:", "option "))
        or "subprocess-exited-with-error" in line.lower()
        or "failed building wheel" in line.lower()
    ]
    selected = preferred[-3:] if preferred else lines[-3:]
    summary = " | ".join(dict.fromkeys(selected))
    return summary if len(summary) <= 900 else summary[:897] + "..."


def _install_jieba_fast_wheel(python_exe, progress_callback):
    progress_callback = _MonotonicProgressReporter(progress_callback)
    version = _get_version(python_exe)
    architecture = _run(
        [
            python_exe,
            "-c",
            "import struct; print(struct.calcsize('P') * 8)",
        ]
    )
    is_64_bit = (
        architecture is not None
        and architecture.returncode == 0
        and (architecture.stdout or "").strip() == "64"
    )
    if version[:2] != TARGET_PYTHON or not is_64_bit:
        detected = _fmt_ver(version) if version != (0, 0, 0) else "未知版本"
        return (
            False,
            f"预编译 wheel 仅支持 64 位 Python 3.11，当前解释器为 {detected}",
        )

    urls = _resource_urls(JIEBA_FAST_WHEEL_NAME)
    if not urls:
        return False, f"resc.net.txt 中未找到 {JIEBA_FAST_WHEEL_NAME}"

    with tempfile.TemporaryDirectory(prefix="aemeath-jieba-fast-") as temp_dir:
        wheel_path = Path(temp_dir) / JIEBA_FAST_WHEEL_NAME
        part_path = wheel_path.with_name(wheel_path.name + ".part")
        last_failure = "wheel 下载失败"
        for index, url in enumerate(urls, start=1):
            source_name = RESOURCE_SOURCE_HOSTS.get(
                (urllib.parse.urlsplit(url).hostname or "").lower(),
                f"镜像 {index}",
            )
            try:
                _unlink_if_exists(part_path, ignore_errors=True)
                print(
                    f"  下载预编译依赖 [{index}/{len(urls)}]: "
                    f"{JIEBA_FAST_WHEEL_NAME} ({source_name})"
                )
                _stream_download_with_progress(
                    url,
                    part_path,
                    label=JIEBA_FAST_PACKAGE,
                )
                digest = hashlib.sha256(part_path.read_bytes()).hexdigest()
                if digest.lower() != JIEBA_FAST_WHEEL_SHA256:
                    raise ValueError(
                        f"SHA-256 不匹配，期望 {JIEBA_FAST_WHEEL_SHA256}，实际 {digest}"
                    )
                part_path.replace(wheel_path)
                return_code, output = _run_pip_requirement_with_progress(
                    python_exe,
                    wheel_path,
                    progress_callback,
                )
                if return_code == 0:
                    progress_callback(100)
                    return True, ""
                last_failure = f"{source_name}：{_summarize_pip_failure(output)}"
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_failure = f"{source_name}：{exc}"
            finally:
                _unlink_if_exists(part_path, ignore_errors=True)
                _unlink_if_exists(wheel_path, ignore_errors=True)

        return False, last_failure


def _install_one(python_exe, pkg, mirrors, progress_callback):
    """Install one package with mirror fallback and return a concise failure."""
    if pkg == JIEBA_FAST_PACKAGE:
        return _install_jieba_fast_wheel(python_exe, progress_callback)
    if not mirrors:
        return False, "没有可用的 pip 镜像"

    progress_callback = _MonotonicProgressReporter(progress_callback)
    last_failure = "pip 安装失败"
    for mirror in mirrors:
        return_code, output = _run_pip_install_with_progress(
            python_exe,
            pkg,
            mirror,
            progress_callback,
        )
        if return_code == 0:
            progress_callback(100)
            return True, ""

        last_failure = f"{mirror['name']}：{_summarize_pip_failure(output)}"
        combined = output.lower()
        if any(marker in combined for marker in _NOT_FOUND_MARKERS):
            continue
    return False, last_failure


def install_all(python_exe, mirrors):
    _print_stage(3, "检查并安装桌宠运行依赖...")
    existing = []
    missing = []
    total_checks = len(DEPENDENCIES)
    check_display = _DependencyCheckProgressDisplay()
    check_display.update("准备检查", 0, total_checks, force=True)
    for index, (pkg, desc, import_checks) in enumerate(DEPENDENCIES, start=1):
        check_display.update(pkg, index - 1, total_checks)
        if _pkg_installed(python_exe, pkg, import_checks=import_checks):
            existing.append(pkg)
        else:
            missing.append((pkg, desc, import_checks))
        check_display.update(pkg, index, total_checks)

    try:
        from lib.script.gsvmove.rar_backend import is_bundled_unrar_ready

        unrar_ready = is_bundled_unrar_ready()
    except Exception:
        unrar_ready = False
    if unrar_ready:
        existing.append("UnRAR后端")

    check_display.clear()
    missing_names = [item[0] for item in missing]
    if not unrar_ready:
        missing_names.append("UnRAR后端")
    print("  已有依赖：" + (", ".join(existing) if existing else "无"))
    print("  未安装依赖：" + (", ".join(missing_names) if missing_names else "无"))

    total_jobs = len(missing_names)
    display = _DependencyProgressDisplay()
    if total_jobs == 0:
        display.update("无需安装", 100, 0, 0, force=True)
        _print_kind("\n  所有依赖已安装", "ok", prefix=False)
        return True

    failed = []
    failure_details = {}
    completed_jobs = 0
    for pkg, _desc, _import_checks in missing:
        job_completed = completed_jobs
        display.update(
            pkg,
            5,
            job_completed,
            total_jobs,
            force=True,
            reset=True,
        )

        def report(percent, package=pkg, overall=job_completed):
            display.update(package, percent, overall, total_jobs)

        installed, failure_detail = _install_one(
            python_exe,
            pkg,
            mirrors,
            report,
        )
        if not installed:
            failed.append(pkg)
            failure_details[pkg] = failure_detail
        completed_jobs += 1
        display.update(
            pkg,
            100 if installed else 0,
            completed_jobs,
            total_jobs,
            force=True,
        )

    if not unrar_ready:
        job_completed = completed_jobs
        display.update(
            "UnRAR后端",
            5,
            job_completed,
            total_jobs,
            force=True,
            reset=True,
        )
        unrar_progress = _MonotonicProgressReporter(
            lambda percent: display.update(
                "UnRAR后端",
                percent,
                job_completed,
                total_jobs,
            )
        )
        try:
            from lib.script.gsvmove.rar_backend import ensure_bundled_unrar

            def report_unrar(current, total):
                percent = 0 if total <= 0 else int((current / total) * 100)
                unrar_progress(max(5, percent))

            ensure_bundled_unrar(report_unrar)
            completed_jobs += 1
            display.update("UnRAR后端", 100, completed_jobs, total_jobs, force=True)
        except Exception:
            failed.append("UnRAR后端")
            completed_jobs += 1
            display.update(
                "UnRAR后端",
                0,
                completed_jobs,
                total_jobs,
                force=True,
            )

    if not failed:
        _print_kind("\n  所有依赖已安装", "ok", prefix=False)
        return True

    _print_warn(f"\n  以下依赖安装失败: {', '.join(failed)}")
    if failure_details:
        print("  失败原因：")
        for name in failed:
            detail = failure_details.get(name)
            if detail:
                print(f"    - {name}: {detail}")
    pip_failed = [name for name in failed if name != "UnRAR后端"]
    if pip_failed:
        binary_only = [name for name in pip_failed if name in BINARY_ONLY_PACKAGES]
        manual_args = ["install"]
        if binary_only:
            manual_args.extend(("--only-binary", ",".join(binary_only)))
        manual_args.extend(PACKAGE_REQUIREMENTS.get(name, name) for name in pip_failed)
        print("  可手动执行以下命令：")
        print("    " + " ".join(_python_module_cmd(python_exe, "pip", *manual_args)))
    if "UnRAR后端" in failed:
        print("  随程序提供的 UnRAR 后端缺失，请重新解压完整桌宠程序包。")
    if os.environ.get("FLYING_SNOW_INSTALLER") == "1":
        print("  安装器模式：依赖存在失败项，继续准备 DSH、语音和资源阶段", flush=True)
        return True
    ans = input("\n仍要继续启动吗? (y/n): ").strip().lower()
    return ans == "y"


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


def _ping_once_ms(host: str, timeout: float = RESOURCE_PING_TIMEOUT_SECONDS) -> float | None:
    timeout = max(0.1, float(timeout))
    if os.name == "nt":
        command = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        command = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout)))), host]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="ignore",
            timeout=timeout + 1.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = result.stdout or ""
    matches = re.findall(r"(?:time|\u65f6\u95f4)?\s*[=<]\s*(\d+(?:\.\d+)?)\s*ms", output, flags=re.IGNORECASE)
    if not matches:
        return None
    try:
        latency = float(matches[-1])
    except (TypeError, ValueError):
        return None
    if "<" in output and latency <= 1.0:
        return 0.5
    return max(0.0, latency)


def _ping_host_average_ms(
    host: str,
    *,
    attempts: int = RESOURCE_PING_ATTEMPTS,
    timeout: float = RESOURCE_PING_TIMEOUT_SECONDS,
) -> float | None:
    attempts = max(1, int(attempts))
    timeout = max(0.1, float(timeout))
    samples = [_ping_once_ms(host, timeout=timeout) for _ in range(attempts)]
    if all(sample is None for sample in samples):
        return None
    timeout_penalty_ms = timeout * 1000.0
    normalized = [timeout_penalty_ms if sample is None else sample for sample in samples]
    return sum(normalized) / attempts


def _benchmark_resource_sources() -> tuple[str, ...]:
    global _RESOURCE_SOURCE_ORDER
    if _RESOURCE_SOURCE_ORDER is not None:
        return _RESOURCE_SOURCE_ORDER

    hosts = tuple(RESOURCE_SOURCE_HOSTS)
    print("\n  正在测速资源下载源（各 3 次，单次超时 5 秒）...")
    with ThreadPoolExecutor(max_workers=len(hosts), thread_name_prefix="resource-ping") as executor:
        futures = {host: executor.submit(_ping_host_average_ms, host) for host in hosts}
        scores = {host: futures[host].result() for host in hosts}

    for host in hosts:
        label = RESOURCE_SOURCE_HOSTS[host]
        latency = scores[host]
        if latency is None:
            print(f"    {label:<6} unreachable")
        else:
            print(f"    {label:<6} {latency:>7.1f} ms average")

    _RESOURCE_SOURCE_ORDER = tuple(
        sorted(
            hosts,
            key=lambda host: (
                scores[host] is None,
                float("inf") if scores[host] is None else scores[host],
                hosts.index(host),
            ),
        )
    )
    selected_host = _RESOURCE_SOURCE_ORDER[0]
    selected_latency = scores[selected_host]
    if selected_latency is None:
        _print_warn("  Gitee 与 GitHub 均不可达，将按清单顺序尝试下载")
        _RESOURCE_SOURCE_ORDER = hosts
    else:
        print(f"  资源下载优先源: {RESOURCE_SOURCE_HOSTS[selected_host]}")
    return _RESOURCE_SOURCE_ORDER


def load_resource_links(path: Path = RESOURCE_LINKS_FILE) -> dict[str, tuple[str, ...]]:
    """读取资源清单，兼容完整 URL 以及“基础 URL + 文件名”格式。"""
    if not path.exists():
        return {}

    links: dict[str, list[str]] = {}
    base_urls: list[str] = []
    resource_names: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {}

    for raw_line in lines:
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme in {"http", "https"}:
            resource_name = urllib.parse.unquote(Path(parsed.path).name)
            if not resource_name or parsed.path.endswith("/"):
                base_urls.append(value.rstrip("/") + "/")
            else:
                links.setdefault(resource_name, []).append(value)
            continue
        if "/" in value or "\\" in value:
            continue
        resource_names.append(value)

    for resource_name in resource_names:
        encoded_name = urllib.parse.quote(resource_name)
        for base_url in base_urls:
            links.setdefault(resource_name, []).append(urllib.parse.urljoin(base_url, encoded_name))
    return {name: tuple(urls) for name, urls in links.items()}


def _order_resource_urls(urls: tuple[str, ...]) -> tuple[str, ...]:
    source_hosts = {
        (urllib.parse.urlsplit(url).hostname or "").lower()
        for url in urls
    }
    if not all(host in source_hosts for host in RESOURCE_SOURCE_HOSTS):
        return urls
    source_order = _benchmark_resource_sources()
    host_rank = {host: index for index, host in enumerate(source_order)}
    return tuple(
        sorted(
            urls,
            key=lambda url: host_rank.get((urllib.parse.urlsplit(url).hostname or "").lower(), len(host_rank)),
        )
    )


def _resource_urls(resource_name: str) -> tuple[str, ...]:
    urls = load_resource_links().get(resource_name, ())
    return _order_resource_urls(urls)


def _order_node_urls(urls: tuple[str, ...]) -> tuple[str, ...]:
    """Ping Node mirrors concurrently and prefer the lowest-latency host."""
    global _NODE_SOURCE_ORDER
    if not urls:
        return urls
    hosts = tuple(dict.fromkeys(
        (urllib.parse.urlsplit(url).hostname or "").lower()
        for url in urls
        if urllib.parse.urlsplit(url).hostname
    ))
    if len(hosts) <= 1:
        return urls
    if _NODE_SOURCE_ORDER is None:
        print("\n  正在并发测速 Node 下载镜像（各 3 次，单次超时 5 秒）...")
        with ThreadPoolExecutor(max_workers=len(hosts), thread_name_prefix="node-ping") as executor:
            futures = {host: executor.submit(_ping_host_average_ms, host) for host in hosts}
            scores = {host: futures[host].result() for host in hosts}
        for host in hosts:
            latency = scores[host]
            label = host
            if latency is None:
                print(f"    {label:<36} unreachable")
            else:
                print(f"    {label:<36} {latency:>7.1f} ms average")
        _NODE_SOURCE_ORDER = tuple(sorted(
            hosts,
            key=lambda host: (
                scores[host] is None,
                float("inf") if scores[host] is None else scores[host],
                hosts.index(host),
            ),
        ))
        selected = _NODE_SOURCE_ORDER[0]
        if scores[selected] is not None:
            print(f"  Node 下载优先源: {selected}")
        else:
            _NODE_SOURCE_ORDER = hosts
            _print_warn("  Node 镜像均不可达，将按清单顺序尝试下载")
    host_rank = {host: index for index, host in enumerate(_NODE_SOURCE_ORDER)}
    return tuple(sorted(
        urls,
        key=lambda url: host_rank.get(
            (urllib.parse.urlsplit(url).hostname or "").lower(),
            len(host_rank),
        ),
    ))

def _format_bytes(num_bytes):
    size = float(max(0, int(num_bytes or 0)))
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}GB"


def _render_transfer_progress(prefix, current, total, start_time):
    elapsed = max(time.perf_counter() - start_time, 1e-6)
    speed = current / elapsed
    speed_text = f"{_format_bytes(speed)}/s"
    current_text = _format_bytes(current)
    if total:
        percent = min(100.0, (current * 100.0) / total)
        total_text = _format_bytes(total)
        bar = _render_dependency_bar(
            current,
            total,
            width=26,
            color_kind="progress_current",
        )
        value = _fmt_color(f"{percent:>6.2f}%", "progress_value")
        return f"{prefix} {bar} {value} {current_text}/{total_text} {speed_text}"
    return f"{prefix} {current_text} {speed_text}"


def _write_progress_line(text: str, *, finish: bool = False) -> None:
    if os.environ.get("FLYING_SNOW_INSTALLER") == "1":
        # Pipe consumers cannot render carriage-return updates reliably.
        # Preserve every transfer sample as a flushed record for the GUI.
        print(text.strip(), flush=True)
        return
    suffix = "\n" if finish else ""
    sys.stdout.write("\r" + text.ljust(120) + suffix)
    sys.stdout.flush()


def _unlink_if_exists(path, *, ignore_errors=False):
    if not path.exists():
        return
    try:
        path.unlink()
    except Exception:
        if not ignore_errors:
            raise


def _rmtree_if_exists(path, *, ignore_errors=True):
    if path.exists():
        shutil.rmtree(path, ignore_errors=ignore_errors)


def _cleanup_vosk_temp_artifacts(archive_path, part_path, extract_root, *, ignore_errors=False):
    _rmtree_if_exists(extract_root, ignore_errors=ignore_errors)
    _unlink_if_exists(part_path, ignore_errors=ignore_errors)
    _unlink_if_exists(archive_path, ignore_errors=ignore_errors)


def _service_bundle_ready(service_dir: Path, required_files) -> bool:
    if not service_dir.exists() or not service_dir.is_dir():
        return False
    for name in required_files:
        if not (service_dir / name).exists():
            return False
    return True


def _stream_download_with_progress(url, dest_path, *, label, timeout=30, chunk_size=256 * 1024, use_env_proxy=True):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    _unlink_if_exists(dest_path)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "FlyingSnowVelvetInstaller/1.0",
            "Accept": "application/zip, application/octet-stream, */*",
        },
    )
    proxy_text = "env-proxy" if use_env_proxy else "direct"
    print(f"    source: {label} ({proxy_text})")

    start_time = time.perf_counter()
    last_draw = 0.0
    opener = urllib.request.build_opener() if use_env_proxy else urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response, open(dest_path, "wb") as fp:
        total_header = response.headers.get("Content-Length")
        total = int(total_header) if total_header and total_header.isdigit() else 0
        current = 0
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            fp.write(chunk)
            current += len(chunk)
            now = time.perf_counter()
            if now - last_draw >= 0.12:
                _write_progress_line(_render_transfer_progress("    downloading", current, total, start_time))
                last_draw = now

        _write_progress_line(
            _render_transfer_progress("    downloading", current, total, start_time),
            finish=True,
        )

    final_size = dest_path.stat().st_size if dest_path.exists() else 0
    if total and final_size != total:
        raise IOError(f"download incomplete: {final_size}/{total} bytes")


def _download_resource_file(
    resource_name: str,
    dest_path: Path,
    *,
    label: str,
    display_sequence: tuple[int, int] | None = None,
) -> bool:
    """资源缺失时按 resc.net.txt 中的同名链接下载。"""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        return True

    urls = _resource_urls(resource_name)
    if not urls:
        _print_warn(f"  resc.net.txt 中未找到资源链接: {resource_name}")
        return False

    part_path = dest_path.with_name(dest_path.name + ".part")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    for index, url in enumerate(urls, start=1):
        try:
            _unlink_if_exists(part_path, ignore_errors=True)
            if display_sequence is None:
                sequence_text = f"[{index}/{len(urls)}]"
            else:
                sequence_text = f"[{display_sequence[0]}/{display_sequence[1]}]"
            print(f"  下载 {label} {sequence_text}: {resource_name}")
            _stream_download_with_progress(url, part_path, label=label)
            part_path.replace(dest_path)
            return True
        except (urllib.error.URLError, OSError) as exc:
            _print_warn(f"  下载失败 [{resource_name}]: {exc}")
        finally:
            _unlink_if_exists(part_path, ignore_errors=True)

    return False


def _extract_zip_with_progress(zip_path, extract_root):
    _rmtree_if_exists(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)
    resolved_root = extract_root.resolve()

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        total = sum(max(0, item.file_size) for item in members if not item.is_dir())
        current = 0
        start_time = time.perf_counter()
        last_draw = 0.0

        for item in members:
            member_name = item.filename.replace("\\", "/")
            if not item.flag_bits & 0x800:
                try:
                    member_name = member_name.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            relative_path = Path(*[part for part in member_name.split("/") if part not in {"", "."}])
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError(f"unsafe zip member path: {item.filename}")
            target_path = (extract_root / relative_path).resolve()
            if target_path != resolved_root and resolved_root not in target_path.parents:
                raise ValueError(f"unsafe zip member path: {item.filename}")

            if item.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(item, "r") as source, open(target_path, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                current += max(0, item.file_size)
            now = time.perf_counter()
            if now - last_draw >= 0.12:
                _write_progress_line(_render_transfer_progress("    extracting ", current, total, start_time))
                last_draw = now

        _write_progress_line(
            _render_transfer_progress("    extracting ", current, total, start_time),
            finish=True,
        )


def _node_version_from_result(result) -> str:
    if result is None or result.returncode != 0:
        return ""
    lines = [line.strip() for line in str(result.stdout or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _node_tree_ready(root: Path) -> tuple[bool, str]:
    """Validate the generated Node tree without invoking npm from the app."""
    root = Path(root)
    node = root / "node.exe"
    npm_cli = root / "node_modules" / "npm" / "bin" / "npm-cli.js"
    if not node.is_file() or not npm_cli.is_file():
        return False, "Node/npm 文件不完整"

    node_result = _run([str(node), "--version"], timeout=30)
    if _node_version_from_result(node_result) != dsh_config.NODE_VERSION_TEXT:
        return False, f"Node 版本不匹配（需要 {dsh_config.NODE_VERSION_TEXT}）"

    npm_result = _run([str(node), str(npm_cli), "--version"], timeout=30)
    if _node_version_from_result(npm_result) != dsh_config.NPM_VERSION:
        return False, f"npm 版本不匹配（需要 {dsh_config.NPM_VERSION}）"
    return True, ""


def _dsh_runtime_ready() -> tuple[bool, str]:
    """Return whether the installed Node and locked DSH tree are usable."""
    source_error = dsh_config.runtime_source_error(PROJECT_ROOT)
    if source_error:
        return False, source_error
    node_root = dsh_config.node_root(PROJECT_ROOT)
    ready, detail = _node_tree_ready(node_root)
    if not ready:
        return False, detail
    installed_error = dsh_config.installed_runtime_error(PROJECT_ROOT)
    return (False, installed_error) if installed_error else (True, "")


def _dsh_node_urls() -> tuple[str, ...]:
    """Use the resource manifest first, then keep official URLs as a fallback."""
    manifest_urls = ()
    try:
        manifest_urls = tuple(_resource_urls(dsh_config.NODE_ARCHIVE_NAME))
    except Exception:
        manifest_urls = ()
    urls = tuple(dict.fromkeys((*manifest_urls, *dsh_config.NODE_DOWNLOAD_URLS)))
    return _order_node_urls(urls)


def _run_dsh_npm_ci() -> tuple[bool, str]:
    node_root = dsh_config.node_root(PROJECT_ROOT)
    node = dsh_config.node_executable(PROJECT_ROOT)
    npm_cli = dsh_config.npm_cli_path(PROJECT_ROOT)
    runtime_root = dsh_config.dsh_runtime_root(PROJECT_ROOT)
    command = [
        str(node),
        str(npm_cli),
        "ci",
        "--omit=dev",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    ]
    print("  使用随包 npm 安装 DSH lockfile 依赖（不执行生命周期脚本）")
    return_code, output = _run_command_with_progress(
        command,
        label="DSH npm ci",
        kind="npm",
        timeout=DSH_RUNTIME_INSTALL_TIMEOUT,
        cwd=runtime_root,
    )
    if return_code == 0:
        return True, ""
    return False, _summarize_pip_failure(output or "npm 未返回错误详情")


def ensure_dsh_office_runtime() -> bool:
    """Install the fixed Node/DSH runtime; runtime startup never installs it."""
    _print_stage(4, "准备 DSH 办公运行时依赖...")
    ready, detail = _dsh_runtime_ready()
    if ready:
        print(
            f"  DSH 办公运行时已就绪: Node {dsh_config.NODE_VERSION_TEXT}, "
            f"npm {dsh_config.NPM_VERSION}, DSH {dsh_config.DSH_VERSION}"
        )
        return True
    if detail:
        print(f"  需要准备 DSH 运行时：{detail}")

    node_root = dsh_config.node_root(PROJECT_ROOT)
    source_error = dsh_config.runtime_source_error(PROJECT_ROOT)
    if source_error:
        _print_warn(f"  无法安装 DSH 办公运行时：{source_error}")
        return False

    node_ready, node_detail = _node_tree_ready(node_root)
    if node_ready:
        installed, install_detail = _run_dsh_npm_ci()
        if not installed:
            _print_warn(f"  DSH lockfile 依赖安装失败：{install_detail}")
            return False
        ready, ready_detail = _dsh_runtime_ready()
        if not ready:
            _print_warn(f"  DSH 安装后完整性检查失败：{ready_detail}")
            return False
        print(
            f"  DSH 办公运行时已修复: Node {dsh_config.NODE_VERSION_TEXT}, "
            f"npm {dsh_config.NPM_VERSION}, DSH {dsh_config.DSH_VERSION}"
        )
        return True

    if node_detail:
        print(f"  需要重新准备 Node 运行时：{node_detail}")
    archive_name = dsh_config.NODE_ARCHIVE_NAME
    urls = _dsh_node_urls()
    if not urls:
        _print_warn(f"  没有可用的 Node 下载地址: {archive_name}")
        return False

    node_root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = node_root.with_name(f".{node_root.name}.installing")
    _rmtree_if_exists(staging_root, ignore_errors=True)
    last_detail = "Node ZIP 下载失败"
    try:
        with tempfile.TemporaryDirectory(prefix="aemeath-dsh-node-") as temp_dir:
            archive_path = Path(temp_dir) / archive_name
            part_path = archive_path.with_name(archive_path.name + ".part")
            for index, url in enumerate(urls, start=1):
                try:
                    source_name = RESOURCE_SOURCE_HOSTS.get(
                        (urllib.parse.urlsplit(url).hostname or "").lower(),
                        f"镜像 {index}",
                    )
                    print(f"  下载 Node 运行时 [{index}/{len(urls)}] ({source_name})")
                    _unlink_if_exists(part_path, ignore_errors=True)
                    _stream_download_with_progress(
                        url,
                        part_path,
                        label=f"Node {dsh_config.NODE_VERSION_TEXT}",
                    )
                    digest = hashlib.sha256(part_path.read_bytes()).hexdigest()
                    if digest.lower() != dsh_config.NODE_ARCHIVE_SHA256.lower():
                        raise ValueError(
                            "Node ZIP SHA-256 不匹配，"
                            f"期望 {dsh_config.NODE_ARCHIVE_SHA256}，实际 {digest}"
                        )
                    part_path.replace(archive_path)
                    break
                except (urllib.error.URLError, OSError, ValueError, zipfile.BadZipFile) as exc:
                    last_detail = f"{source_name}：{exc}"
                    _print_warn(f"  Node 下载/校验失败：{last_detail}")
                    _unlink_if_exists(part_path, ignore_errors=True)
            else:
                _print_warn(f"  Node 运行时准备失败：{last_detail}")
                return False

            extract_root = Path(temp_dir) / "extract"
            _extract_zip_with_progress(archive_path, extract_root)
            source_root = extract_root / f"node-v{dsh_config.NODE_VERSION}-win-x64"
            if not source_root.is_dir():
                raise ValueError(f"Node ZIP 缺少目录 {source_root.name}")
            shutil.move(str(source_root), str(staging_root))

        ready, detail = _node_tree_ready(staging_root)
        if not ready:
            raise RuntimeError(f"解压后的 Node 运行时无效：{detail}")

        old_root = node_root.with_name(f".{node_root.name}.previous")
        _rmtree_if_exists(old_root, ignore_errors=True)
        if node_root.exists():
            node_root.rename(old_root)
        try:
            staging_root.rename(node_root)
        except Exception:
            if old_root.exists() and not node_root.exists():
                old_root.rename(node_root)
            raise
        _rmtree_if_exists(old_root, ignore_errors=True)

        installed, detail = _node_tree_ready(node_root)
        if not installed:
            raise RuntimeError(detail)
        installed, detail = _run_dsh_npm_ci()
        if not installed:
            raise RuntimeError(detail)
        ready, detail = _dsh_runtime_ready()
        if not ready:
            raise RuntimeError(f"DSH 安装后完整性检查失败：{detail}")
        print(
            f"  DSH 办公运行时已安装: Node {dsh_config.NODE_VERSION_TEXT}, "
            f"npm {dsh_config.NPM_VERSION}, DSH {dsh_config.DSH_VERSION}"
        )
        return True
    except Exception as exc:
        _print_warn(f"  DSH 办公运行时安装失败：{exc}")
        _rmtree_if_exists(staging_root, ignore_errors=True)
        return False


def _seanima_ready() -> bool:
    return SEANIMA_TARGET_DIR.is_dir() and any(SEANIMA_TARGET_DIR.rglob("*.webp"))


def ensure_seanima_assets() -> bool:
    """确保启动/退出动画序列帧存在。"""
    _print_stage(7, "准备启动/退出动画资源...")
    if _seanima_ready():
        print(f"  动画资源已存在: {SEANIMA_TARGET_DIR.relative_to(PROJECT_ROOT)}")
        return True

    if not _download_resource_file(SEANIMA_RESOURCE_NAME, SEANIMA_ARCHIVE, label="启动动画资源"):
        return False

    temp_root = Path(os.environ.get("TEMP", "C:\\Temp")) / "fsv_seanima"
    extract_root = temp_root / "extract"
    _rmtree_if_exists(temp_root, ignore_errors=True)
    try:
        _extract_zip_with_progress(SEANIMA_ARCHIVE, extract_root)
        source_root = extract_root / "SEanima"
        if not source_root.is_dir():
            directories = [path for path in extract_root.iterdir() if path.is_dir()]
            if len(directories) == 1:
                source_root = directories[0]
        if not source_root.is_dir() or not any(source_root.rglob("*.webp")):
            raise FileNotFoundError("动画资源包中未找到 SEanima 序列帧目录")
        _rmtree_if_exists(SEANIMA_TARGET_DIR, ignore_errors=True)
        SEANIMA_TARGET_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_root), str(SEANIMA_TARGET_DIR))
        print(f"  动画资源已安装: {SEANIMA_TARGET_DIR.relative_to(PROJECT_ROOT)}")
        return True
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        _print_warn(f"  安装动画资源失败: {exc}")
        return False
    finally:
        _rmtree_if_exists(temp_root, ignore_errors=True)
        _unlink_if_exists(SEANIMA_ARCHIVE, ignore_errors=True)


def _resolve_vosk_model_source_dir(extract_root):
    if all((extract_root / marker).exists() for marker in ("am", "conf")):
        return extract_root

    children = [item for item in extract_root.iterdir() if item.is_dir()]
    for child in children:
        if all((child / marker).exists() for marker in ("am", "conf")):
            return child

    if len(children) == 1:
        return children[0]

    raise FileNotFoundError("extracted model folder not found")


def _microphone_runtime_ready(python_exe):
    return (
        _pkg_installed(python_exe, "sounddevice", import_checks=("sounddevice",))
        and _pkg_installed(python_exe, "vosk", import_checks=("vosk",))
        and _pkg_installed(python_exe, "webrtcvad-wheels", import_checks=("webrtcvad",))
    )


def _ensure_single_vosk_model(spec: dict) -> bool:
    label = spec.get("label") or spec["name"]
    resource_name = spec["resource_name"]
    target_dir = VOSK_MODELS_DIR / spec["name"]
    rel_target = target_dir.relative_to(PROJECT_ROOT)

    if all((target_dir / marker).exists() for marker in VOSK_MODEL_MARKERS):
        print(f"  model already installed ({label}): {rel_target}")
        return True

    archive_path = VOSK_MODELS_DIR / f"{spec['name']}.zip"
    part_path = VOSK_MODELS_DIR / f"{spec['name']}.zip.part"
    extract_root = VOSK_MODELS_DIR / f"_{spec['name']}_extract"
    VOSK_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    for leftover in VOSK_MODELS_DIR.glob("BIT*.tmp"):
        _unlink_if_exists(leftover, ignore_errors=True)

    try:
        _cleanup_vosk_temp_artifacts(archive_path, part_path, extract_root)
        if not _download_resource_file(resource_name, archive_path, label=f"Vosk {label} 模型"):
            return False
        _extract_zip_with_progress(archive_path, extract_root)
        source_dir = _resolve_vosk_model_source_dir(extract_root)

        _rmtree_if_exists(target_dir)
        shutil.move(str(source_dir), str(target_dir))
        print(f"    model installed: {rel_target}")
        return True
    except (OSError, ValueError, zipfile.BadZipFile, FileNotFoundError) as exc:
        print(f"    failed: {exc}")
        print(f"  warning: {label} model auto download failed")
        print(f"  extract target: {rel_target}")
        return False
    finally:
        _cleanup_vosk_temp_artifacts(archive_path, part_path, extract_root, ignore_errors=True)


def ensure_vosk_models():
    _print_stage(6, "准备 Vosk 语音模型...")
    all_ok = True
    for spec in VOSK_MODEL_SPECS:
        if not _ensure_single_vosk_model(spec):
            all_ok = False
    return all_ok


def launch(python_exe):
    """Launch main script, prefer pythonw if available."""
    _print_stage(10, "启动飞行雪绒桌宠...")

    main_script = PROJECT_ROOT / "lib" / "core" / "qt_desktop_pet.py"
    if not main_script.exists():
        print(f"  main script not found: {main_script}")
        return False

    launcher = _resolve_pythonw_path(python_exe, fallback=python_exe)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        subprocess.Popen(
            [launcher, str(main_script)],
            cwd=str(PROJECT_ROOT),
            env=env,
            creationflags=create_no_window,
        )
        print("  launched in background")
        return True
    except Exception as e:
        print(f"  launch failed: {e}")
        return False


def main():
    print("=" * 56)
    print(" Flying Snow Velvet LTS - Install and Launch")
    print("=" * 56)
    print()

    try:
        python_exe, pip_ok = select_best_python()

        if not pip_ok:
            if not ensure_pip(python_exe):
                input(_fmt_color("\n[错误] 无法自动安装 pip，按回车退出...", "error"))
                sys.exit(1)

        save_config(python_exe)

        mirrors = benchmark_mirrors()

        if not install_all(python_exe, mirrors):
            _print_warn("依赖未全部安装，可能影响部分功能")

        if not ensure_dsh_office_runtime():
            _print_warn("DSH 办公运行时未准备完成，办公模式将提示重新运行安装依赖")

        _print_stage(5, "选择并准备可选 ONNX 语音 GPU 运行时...")
        use_cuda, use_directml = choose_voice_gpu_runtimes()
        if use_cuda and not ensure_cuda_voice_runtime(python_exe, mirrors):
            _print_warn("NVIDIA CUDA 运行时未准备完成，设置页不会显示 N 卡加速")
        if use_directml and not ensure_directml_hybrid_runtime(python_exe, mirrors):
            _print_warn("DirectML 混合推理环境未准备完成，ONNX 语音将使用 CPU")

        if _microphone_runtime_ready(python_exe):
            if not ensure_vosk_models():
                _print_warn("部分 Vosk 模型缺失，语音识别可能无法正常工作")
        else:
            _print_stage(6, "跳过 Vosk 模型下载（sounddevice/vosk 未就绪）")

        if not ensure_seanima_assets():
            _print_warn("启动/退出动画资源未准备完成，将按程序兼容逻辑继续启动")

        if os.environ.get("FLYING_SNOW_INSTALLER") == "1":
            print("\n安装器模式：依赖与资源准备完成，交由安装器启动桌宠。", flush=True)
            return
        if launch(python_exe):
            print("\nLauncher will close in 3 seconds...")
            time.sleep(3)
        else:
            input("\nPress Enter to exit...")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n[cancelled] interrupted by user")
    except Exception as e:
        _print_error(f"\n发生未预期异常: {e}")
        import traceback

        traceback.print_exc()
        input("Press Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    main()

