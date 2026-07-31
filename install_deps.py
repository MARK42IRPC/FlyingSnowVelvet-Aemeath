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
6. 按 resc.net.txt 下载缺失的 Vosk、动画和浏览器资源.
7. 准备本地网页中转服务源码.
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

from config import voice_runtime as directml_config

PROJECT_ROOT = Path(__file__).parent
RESOURCE_LINKS_FILE = PROJECT_ROOT / "resc.net.txt"
RESOURCE_SOURCE_HOSTS = {
    "gitee.com": "Gitee",
    "github.com": "GitHub",
}
RESOURCE_PING_ATTEMPTS = 3
RESOURCE_PING_TIMEOUT_SECONDS = 5.0
_RESOURCE_SOURCE_ORDER: tuple[str, ...] | None = None

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
    ("uvicorn", "ASGI server for local web relay", ("uvicorn",)),
    ("vosk", "offline speech-to-text engine", ("vosk",)),
]

TOTAL_STEPS = 8

YUANBAO_SERVICE_REPO_ZIP = "https://github.com/chenwr727/yuanbao-free-api/archive/refs/heads/main.zip"
YUANBAO_SERVICE_REPO_ZIP_FALLBACKS = (
    YUANBAO_SERVICE_REPO_ZIP,
    "https://codeload.github.com/chenwr727/yuanbao-free-api/zip/refs/heads/main",
)
YUANBAO_SERVICE_BUNDLED_ZIP = PROJECT_ROOT / "services" / "bundles" / "yuanbao-free-api-main.zip"
YUANBAO_SERVICE_DIR = PROJECT_ROOT / "services" / "yuanbao-free-api"
YUANBAO_SERVICE_REQUIRED_FILES = ("app.py", "requirements.txt")
PLAYWRIGHT_RUNTIME_ROOT = PROJECT_ROOT / "resc" / "playwright"
PLAYWRIGHT_BROWSERS_ROOT = PLAYWRIGHT_RUNTIME_ROOT / "browsers"
PLAYWRIGHT_CHROMIUM_REVISION = "1208"
PLAYWRIGHT_RUNTIME_RESOURCE_NAMES = (
    "chrome-runtime.z01",
    "chrome-runtime.z02",
    "chrome-runtime.zip",
)
PLAYWRIGHT_RUNTIME_ARCHIVE = PROJECT_ROOT / "resc" / PLAYWRIGHT_RUNTIME_RESOURCE_NAMES[-1]
PLAYWRIGHT_RUNTIME_TARGET_DIR = PLAYWRIGHT_BROWSERS_ROOT / "ms-playwright" / f"chromium-{PLAYWRIGHT_CHROMIUM_REVISION}"
PLAYWRIGHT_LOCAL_BROWSER_MARKERS = (
    ("ms-playwright", "chromium-*", "chrome-win64", "chrome.exe"),
    ("ms-playwright", "chromium-*", "chrome-linux", "chrome"),
    ("ms-playwright", "chromium-*", "chrome-mac", "Chromium.app"),
)


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

_NOT_FOUND_MARKERS = (
    "no matching distribution found",
    "could not find a version that satisfies",
    "no distributions at all",
)


def _run(cmd, timeout=12):
    """Run command quietly. Return CompletedProcess or None."""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
        )
    except Exception:
        return None


def _python_module_cmd(python_exe, module, *args):
    return [python_exe, "-m", module, *args]


def _run_python_module(python_exe, module, *args, timeout=12):
    return _run(_python_module_cmd(python_exe, module, *args), timeout=timeout)


def _run_pip(python_exe, *args, timeout=12):
    return _run_python_module(python_exe, "pip", *args, timeout=timeout)


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
        if key not in seen:
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
        probed = _probe_python_info(exe)
        if not probed:
            print(f"  [skip] Python probe failed: {_console_safe(exe)}")
            continue

        resolved_exe, ver = probed
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
    _print_stage(2, "测试依赖镜像延迟...")
    scored = []

    for mirror in PYPI_MIRRORS:
        lat = _tcp_ms(mirror["host"])
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

    def update(self, package, package_percent, overall_current, overall_total, *, force=False):
        percent = max(0, min(100, int(package_percent)))
        payload = (str(package), percent, int(overall_current), int(overall_total))
        if payload == self._last_payload and not force:
            return
        self._last_payload = payload
        package_bar = _render_dependency_bar(
            percent,
            100,
            color_kind="progress_current",
        )
        overall_bar = _render_dependency_bar(
            overall_current,
            overall_total,
            color_kind="progress_overall",
        )
        current_label = _fmt_color("当前依赖", "info")
        overall_label = _fmt_color("整体进度", "stage")
        package_value = _fmt_color(f"{percent:>3}%", "progress_value")
        overall_value = _fmt_color(
            f"{overall_current}/{overall_total}",
            "progress_value",
        )
        package_name = _fmt_color(str(package), "progress_current")
        first = f"  {current_label} {package_bar} {package_value}  {package_name}"
        second = f"  {overall_label} {overall_bar} {overall_value}"

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


def _run_pip_requirement_with_progress(
    python_exe,
    requirement,
    progress_callback,
    *,
    mirror=None,
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
    started_at = time.monotonic()
    percent = 1
    reader_done = False
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

        elapsed = time.monotonic() - started_at
        estimated = min(90, 2 + int(elapsed * 3))
        percent = max(percent, estimated)
        progress_callback(percent)

    reader.join(timeout=1.0)
    return proc.returncode, "".join(output_tail)


def _run_pip_install_with_progress(python_exe, pkg, mirror, progress_callback):
    return _run_pip_requirement_with_progress(
        python_exe,
        pkg,
        progress_callback,
        mirror=mirror,
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
    current_job = 0
    for pkg, _desc, _import_checks in missing:
        current_job += 1
        display.update(pkg, 1, current_job, total_jobs)

        def report(percent, package=pkg, index=current_job):
            display.update(package, percent, index, total_jobs)

        installed, failure_detail = _install_one(
            python_exe,
            pkg,
            mirrors,
            report,
        )
        if not installed:
            failed.append(pkg)
            failure_details[pkg] = failure_detail
        display.update(pkg, 100 if pkg not in failed else 0, current_job, total_jobs, force=True)

    if not unrar_ready:
        current_job += 1
        display.update("UnRAR后端", 1, current_job, total_jobs)
        try:
            from lib.script.gsvmove.rar_backend import ensure_bundled_unrar

            def report_unrar(current, total):
                percent = 0 if total <= 0 else int((current / total) * 100)
                display.update("UnRAR后端", percent, current_job, total_jobs)

            ensure_bundled_unrar(report_unrar)
            display.update("UnRAR后端", 100, current_job, total_jobs, force=True)
        except Exception:
            failed.append("UnRAR后端")
            display.update("UnRAR后端", 0, current_job, total_jobs, force=True)

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
        print("  可手动执行以下命令：")
        print("    " + " ".join(_python_module_cmd(python_exe, "pip", "install", *pip_failed)))
    if "UnRAR后端" in failed:
        print("  随程序提供的 UnRAR 后端缺失，请重新解压完整桌宠程序包。")
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
    _print_stage(4, "准备 DirectML GPU 混合推理环境...")
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
        bar_width = 24
        filled = max(0, min(bar_width, int(percent / 100.0 * bar_width)))
        bar = "#" * filled + "-" * (bar_width - filled)
        return f"{prefix} [{bar}] {percent:6.2f}% {current_text}/{total_text} {speed_text}"
    return f"{prefix} {current_text} {speed_text}"


def _write_progress_line(text: str, *, finish: bool = False) -> None:
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


def _candidate_playwright_browser_executables() -> list[Path]:
    candidates: list[Path] = []
    for marker in PLAYWRIGHT_LOCAL_BROWSER_MARKERS:
        pattern = PLAYWRIGHT_BROWSERS_ROOT.joinpath(*marker)
        try:
            matches = sorted((Path(item) for item in glob.glob(str(pattern))), reverse=True)
        except Exception:
            matches = []
        candidates.extend(matches)
    return candidates


def _find_playwright_browser_runtime() -> Optional[Path]:
    for candidate in _candidate_playwright_browser_executables():
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


def _find_bundle_root(extract_root: Path, required_files) -> Optional[Path]:
    candidates = [extract_root]
    candidates.extend(path for path in extract_root.iterdir() if path.is_dir())
    for candidate in candidates:
        if all((candidate / name).exists() for name in required_files):
            return candidate
    for candidate in extract_root.rglob('*'):
        if candidate.is_dir() and all((candidate / name).exists() for name in required_files):
            return candidate
    return None


def _download_yuanbao_service_bundle() -> bool:
    if _service_bundle_ready(YUANBAO_SERVICE_DIR, YUANBAO_SERVICE_REQUIRED_FILES):
        print(f"  已存在服务目录: {YUANBAO_SERVICE_DIR}")
        return True

    def _install_from_archive(archive_path: Path, source_text: str) -> bool:
        temp_root = Path(os.environ.get("TEMP", "C:\\Temp")) / "fsv_yuanbao_bundle"
        extract_root = temp_root / "extract"
        _rmtree_if_exists(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)
        try:
            print(f"  使用 {source_text} 准备本地网页中转服务包...")
            extract_root.mkdir(parents=True, exist_ok=True)
            _extract_zip_with_progress(archive_path, extract_root)
            bundle_root = _find_bundle_root(extract_root, YUANBAO_SERVICE_REQUIRED_FILES)
            if bundle_root is None:
                raise RuntimeError('服务包中未找到 app.py / requirements.txt')
            _rmtree_if_exists(YUANBAO_SERVICE_DIR, ignore_errors=True)
            YUANBAO_SERVICE_DIR.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(bundle_root), str(YUANBAO_SERVICE_DIR))
            print(f"  已安装到: {YUANBAO_SERVICE_DIR}")
            return True
        except Exception as exc:
            _print_warn(f"  安装本地网页中转服务包失败 [{source_text}]: {exc}")
            return False
        finally:
            _rmtree_if_exists(temp_root, ignore_errors=True)

    if YUANBAO_SERVICE_BUNDLED_ZIP.exists():
        if _install_from_archive(YUANBAO_SERVICE_BUNDLED_ZIP, "仓库内置压缩包"):
            return True

    temp_root = Path(os.environ.get("TEMP", "C:\\Temp")) / "fsv_yuanbao_bundle"
    archive_path = temp_root / "yuanbao-free-api-main.zip"
    _rmtree_if_exists(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        print("  下载本地网页中转服务包...")
        last_error = None
        for idx, url in enumerate(YUANBAO_SERVICE_REPO_ZIP_FALLBACKS, start=1):
            _unlink_if_exists(archive_path, ignore_errors=True)
            use_env_proxy = idx == 1
            source_name = f"local-web-relay#{idx}"
            try:
                _stream_download_with_progress(
                    url,
                    archive_path,
                    label=source_name,
                    use_env_proxy=use_env_proxy,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                proxy_mode = "系统代理" if use_env_proxy else "直连(禁用代理)"
                _print_warn(f"  下载源失败 [{proxy_mode}] {url}: {exc}")
        if last_error is not None:
            raise last_error
        return _install_from_archive(archive_path, '在线下载压缩包')
    except Exception as e:
        _print_warn(f"  下载/解压本地网页中转服务失败: {e}")
        return False
    finally:
        _rmtree_if_exists(temp_root, ignore_errors=True)


def ensure_yuanbao_service_bundle() -> bool:
    _print_stage(6, "准备本地网页中转服务...")
    bundle_ok = _download_yuanbao_service_bundle()
    return bundle_ok


def _browser_runtime_resource_paths() -> tuple[Path, ...]:
    return tuple(PROJECT_ROOT / "resc" / name for name in PLAYWRIGHT_RUNTIME_RESOURCE_NAMES)


def _ensure_browser_runtime_archives() -> bool:
    total = len(PLAYWRIGHT_RUNTIME_RESOURCE_NAMES)
    for index, (resource_name, resource_path) in enumerate(
        zip(PLAYWRIGHT_RUNTIME_RESOURCE_NAMES, _browser_runtime_resource_paths()),
        start=1,
    ):
        if not _download_resource_file(
            resource_name,
            resource_path,
            label="浏览器运行时",
            display_sequence=(index, total),
        ):
            return False
    return True


def _merge_split_zip(archive_paths: tuple[Path, ...], merged_path: Path) -> None:
    """将标准 ZIP 分卷合并为 Python zipfile 可读取的单卷 ZIP。"""
    if len(archive_paths) < 2:
        raise ValueError("ZIP 分卷至少需要两个文件")

    volume_offsets: list[int] = []
    total_size = 0
    with open(merged_path, "wb") as output:
        for path in archive_paths:
            volume_offsets.append(total_size)
            with open(path, "rb") as source:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            total_size += path.stat().st_size

    final_path = archive_paths[-1]
    final_size = final_path.stat().st_size
    with open(final_path, "rb") as source:
        source.seek(max(0, final_size - 1024 * 1024))
        final_tail = source.read()
    eocd_relative = final_tail.rfind(b"PK\x05\x06")
    if eocd_relative < 0:
        raise zipfile.BadZipFile("分卷 ZIP 缺少 EOCD")
    eocd_disk_offset = volume_offsets[-1] + final_size - len(final_tail) + eocd_relative

    with open(merged_path, "r+b") as merged:
        merged.seek(eocd_disk_offset)
        eocd = bytearray(merged.read(22))
        if len(eocd) < 22:
            raise zipfile.BadZipFile("分卷 ZIP 的 EOCD 不完整")
        _, disk_number, central_disk, entries_on_disk, total_entries, central_size, central_offset, comment_size = struct.unpack(
            "<4sHHHHIIH", eocd
        )
        if disk_number >= len(volume_offsets) or central_disk >= len(volume_offsets):
            raise zipfile.BadZipFile("不支持的 ZIP 分卷编号")
        if total_entries == 0xFFFF or central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF:
            raise zipfile.BadZipFile("暂不支持 ZIP64 分卷")

        central_start = volume_offsets[central_disk] + central_offset
        merged.seek(central_start)
        entries = []
        for _ in range(total_entries):
            entry_start = merged.tell()
            header = bytearray(merged.read(46))
            if len(header) < 46 or header[:4] != b"PK\x01\x02":
                raise zipfile.BadZipFile("分卷 ZIP 中央目录损坏")
            name_length, extra_length, comment_length = struct.unpack_from("<HHH", header, 28)
            disk_start = struct.unpack_from("<H", header, 34)[0]
            local_offset = struct.unpack_from("<I", header, 42)[0]
            if disk_start >= len(volume_offsets):
                raise zipfile.BadZipFile("分卷 ZIP 本地文件编号无效")
            struct.pack_into("<H", header, 34, 0)
            struct.pack_into("<I", header, 42, volume_offsets[disk_start] + local_offset)
            entries.append((entry_start, bytes(header)))
            merged.seek(name_length + extra_length + comment_length, 1)

        merged.seek(eocd_disk_offset)
        struct.pack_into("<H", eocd, 4, 0)
        struct.pack_into("<H", eocd, 6, 0)
        struct.pack_into("<H", eocd, 8, total_entries)
        struct.pack_into("<I", eocd, 16, central_start)
        merged.write(eocd)

        for entry_start, header in entries:
            merged.seek(entry_start)
            merged.write(header)


def _extract_browser_runtime_archive(extract_root: Path) -> None:
    archive_paths = _browser_runtime_resource_paths()
    split_parts = archive_paths[:-1]
    if not all(path.exists() for path in split_parts):
        _extract_zip_with_progress(PLAYWRIGHT_RUNTIME_ARCHIVE, extract_root)
        return

    combined_archive = extract_root.parent / "chrome-runtime-combined.zip"
    _unlink_if_exists(combined_archive, ignore_errors=True)
    try:
        _merge_split_zip(archive_paths, combined_archive)
        _extract_zip_with_progress(combined_archive, extract_root)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise RuntimeError(f"浏览器分卷包解压失败: {exc}") from exc
    finally:
        _unlink_if_exists(combined_archive, ignore_errors=True)


def _find_extracted_browser_root(extract_root: Path) -> Optional[Path]:
    direct_root = extract_root / "chrome-win64"
    if (direct_root / "chrome.exe").exists():
        return direct_root
    for executable in extract_root.rglob("chrome.exe"):
        if executable.parent.name == "chrome-win64":
            return executable.parent
    return None


def ensure_yuanbao_browser_runtime(python_exe) -> bool:
    _print_stage(7, "准备浏览器离线运行时...")

    runtime_path = _find_playwright_browser_runtime()
    if runtime_path is not None:
        try:
            rel_path = runtime_path.relative_to(PROJECT_ROOT)
        except Exception:
            rel_path = runtime_path
        print(f"  已存在浏览器运行时: {rel_path}")
        return True

    if not _ensure_browser_runtime_archives():
        _print_warn("  浏览器运行时资源下载未完成")
        return False

    print("  使用 resc.net.txt 外置资源部署浏览器运行时")
    temp_root = Path(os.environ.get("TEMP", "C:\\Temp")) / "fsv_playwright_runtime"
    extract_root = temp_root / "extract"
    _rmtree_if_exists(temp_root, ignore_errors=True)
    PLAYWRIGHT_RUNTIME_TARGET_DIR.parent.mkdir(parents=True, exist_ok=True)

    try:
        extract_root.mkdir(parents=True, exist_ok=True)
        _extract_browser_runtime_archive(extract_root)
        extracted_root = _find_extracted_browser_root(extract_root)
        if extracted_root is None:
            raise FileNotFoundError("浏览器资源包中未找到 chrome-win64/chrome.exe")
        _rmtree_if_exists(PLAYWRIGHT_RUNTIME_TARGET_DIR, ignore_errors=True)
        shutil.move(str(extracted_root), str(PLAYWRIGHT_RUNTIME_TARGET_DIR / "chrome-win64"))
    except Exception as exc:
        _print_warn(f"  安装浏览器运行时失败: {exc}")
        return False
    finally:
        _rmtree_if_exists(temp_root, ignore_errors=True)

    runtime_path = _find_playwright_browser_runtime()
    if runtime_path is None:
        _print_warn("  离线安装完成，但未在 resc/playwright 中找到 Chromium 可执行文件")
        return False
    try:
        rel_path = runtime_path.relative_to(PROJECT_ROOT)
    except Exception:
        rel_path = runtime_path
    print(f"  浏览器运行时已安装: {rel_path}")
    for resource_path in _browser_runtime_resource_paths():
        _unlink_if_exists(resource_path, ignore_errors=True)
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


def _seanima_ready() -> bool:
    return SEANIMA_TARGET_DIR.is_dir() and any(SEANIMA_TARGET_DIR.rglob("*.webp"))


def ensure_seanima_assets() -> bool:
    """确保启动/退出动画序列帧存在。"""
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
    _print_stage(5, "准备 Vosk 语音模型...")
    all_ok = True
    for spec in VOSK_MODEL_SPECS:
        if not _ensure_single_vosk_model(spec):
            all_ok = False
    return all_ok


def launch(python_exe):
    """Launch main script, prefer pythonw if available."""
    _print_stage(8, "启动飞行雪绒桌宠...")

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

        if not ensure_directml_hybrid_runtime(python_exe, mirrors):
            _print_warn("DirectML 混合推理环境未准备完成，ONNX 语音将使用 CPU")

        if _microphone_runtime_ready(python_exe):
            if not ensure_vosk_models():
                _print_warn("部分 Vosk 模型缺失，语音识别可能无法正常工作")
        else:
            _print_stage(5, "跳过 Vosk 模型下载（sounddevice/vosk 未就绪）")

        if not ensure_seanima_assets():
            _print_warn("启动/退出动画资源未准备完成，将按程序兼容逻辑继续启动")

        if not ensure_yuanbao_service_bundle():
            _print_warn("本地网页中转服务未准备完成，相关网页模式可能不可用")

        if not ensure_yuanbao_browser_runtime(python_exe):
            _print_warn("浏览器运行时未准备完成，网页登录可能不可用")

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

