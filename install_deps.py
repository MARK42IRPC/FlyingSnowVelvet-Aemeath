# -*- coding: utf-8 -*-
"""Flying Snow Velvet LTS - Install dependencies and launch.

流程:
1. 扫描系统 Python, 选择可用且版本最优的解释器.
2. 若缺少 pip, 自动尝试安装.
3. 评估镜像延迟并按优先级安装依赖.
4. 写入 py.ini:
   - python_executable
   - pythonw_executable
5. 下载 Vosk 中/英文模型到 resc/models/vosk-model-small-*/.
6. 准备 yuanbao-free-api 本地中转服务源码.
7. 启动主程序.
"""

import configparser
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent

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
    ("fastapi", "YuanBao relay API framework", ("fastapi",)),
    ("httpx", "async HTTP client for YuanBao relay", ("httpx",)),
    ("packaging", "version / requirement parsing helpers", ("packaging",)),
    ("openai", "OpenAI-compatible client for YuanBao relay", ("openai",)),
    ("opencv-python", "image preprocessing for YuanBao relay", ("cv2",)),
    ("playwright", "browser automation for YuanBao login capture", ("playwright",)),
    ("pydantic", "data validation for YuanBao relay", ("pydantic",)),
    ("pydantic-settings", "settings loader for YuanBao relay", ("pydantic_settings",)),
    ("pygame", "audio playback", ("pygame",)),
    ("requests", "HTTP client", ("requests",)),
    ("qrcode", "QR code generation for music login", ("qrcode",)),
    ("sse-starlette", "SSE streaming for YuanBao relay", ("sse_starlette",)),
    ("mutagen", "local audio metadata parsing", ("mutagen",)),
    ("pycaw", "Windows audio meter", ("pycaw",)),
    ("comtypes", "COM bindings for pycaw", ("comtypes",)),
    ("pywin32", "Windows COM bridge (win32com/pythoncom)", ("pythoncom", "win32com")),
    ("sounddevice", "microphone capture for speech-to-text", ("sounddevice",)),
    ("uvicorn", "ASGI server for YuanBao relay", ("uvicorn",)),
    ("vosk", "offline speech-to-text engine", ("vosk",)),
]

TOTAL_STEPS = 6

YUANBAO_SERVICE_REPO_ZIP = "https://github.com/chenwr727/yuanbao-free-api/archive/refs/heads/main.zip"
YUANBAO_SERVICE_REPO_ZIP_FALLBACKS = (
    YUANBAO_SERVICE_REPO_ZIP,
    "https://codeload.github.com/chenwr727/yuanbao-free-api/zip/refs/heads/main",
)
YUANBAO_SERVICE_BUNDLED_ZIP = PROJECT_ROOT / "services" / "bundles" / "yuanbao-free-api-main.zip"
YUANBAO_SERVICE_DIR = PROJECT_ROOT / "services" / "yuanbao-free-api"
YUANBAO_SERVICE_REQUIRED_FILES = ("app.py", "requirements.txt")


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
        "urls": (
            {"name": "Official", "url": "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"},
        ),
    },
    {
        "name": "vosk-model-small-en-us-0.15",
        "label": "English",
        "urls": (
            {"name": "Official", "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"},
        ),
    },
)

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
    """Match batch-file selection: current interpreter first, then prefer Python 3.11."""
    ver, exe = item
    current_exe = _current_runtime_executable()
    current = 0 if os.path.normcase(os.path.abspath(exe)) == os.path.normcase(os.path.abspath(current_exe)) else 1
    target_major, target_minor = TARGET_PYTHON
    exact_target = 0 if (ver[0], ver[1]) == TARGET_PYTHON else 1
    distance = abs(ver[1] - target_minor) if ver[0] == target_major else 99
    non_py3 = 0 if ver[0] == target_major else 1
    return (current, exact_target, distance, non_py3, -ver[0], -ver[1], -ver[2], exe.lower())


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


def _pkg_installed(python_exe, pkg, import_checks=()):
    """
    Check package availability by:
    1) pip metadata exists
    2) referenced runtime modules can be imported
    """
    r = _run_pip(python_exe, "show", pkg)
    if not (r is not None and r.returncode == 0):
        return False

    modules = [m for m in import_checks if str(m or "").strip()]
    if not modules:
        return True

    code = "; ".join(f"import {m}" for m in modules)
    ir = _run([python_exe, "-c", code])
    return ir is not None and ir.returncode == 0


def _install_one(python_exe, pkg, mirrors):
    """Install one package with mirror fallback."""
    for i, mirror in enumerate(mirrors):
        label = "primary" if i == 0 else f"backup{i}"
        print(f"    [{label}] {mirror['name']} ...", end=" ", flush=True)

        r = _run_pip(
            python_exe,
            "install",
            pkg,
            "-i",
            mirror["url"],
            "--trusted-host",
            mirror["host"],
            "--no-warn-script-location",
            timeout=240,
        )

        if r and r.returncode == 0:
            print("ok")
            return True

        combined = ((r.stderr or "") + (r.stdout or "")).lower() if r else ""
        if any(marker in combined for marker in _NOT_FOUND_MARKERS):
            print("not found on this mirror, switching")
        else:
            print("failed, switching")

    return False


def install_all(python_exe, mirrors):
    _print_stage(3, "检查并安装桌宠/元宝依赖...")
    failed = []

    for pkg, desc, import_checks in DEPENDENCIES:
        print(f"\n  - {pkg} ({desc})")
        if _pkg_installed(python_exe, pkg, import_checks=import_checks):
            print("    已安装")
            continue

        print("    缺失，正在安装...")
        if not _install_one(python_exe, pkg, mirrors):
            print(f"    安装失败: {pkg}")
            failed.append(pkg)

    if not failed:
        _print_kind("\n  所有依赖已安装", "ok", prefix=False)
        return True

    _print_warn(f"\n  以下依赖安装失败: {', '.join(failed)}")
    print("  可手动执行以下命令：")
    print("    " + " ".join(_python_module_cmd(python_exe, "pip", "install", *failed)))
    ans = input("\n仍要继续启动吗? (y/n): ").strip().lower()
    return ans == "y"


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
            print(f"  使用 {source_text} 准备 yuanbao-free-api 服务包...")
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
            _print_warn(f"  安装 yuanbao-free-api 服务包失败 [{source_text}]: {exc}")
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
        print("  下载 yuanbao-free-api 服务包...")
        last_error = None
        for idx, url in enumerate(YUANBAO_SERVICE_REPO_ZIP_FALLBACKS, start=1):
            _unlink_if_exists(archive_path, ignore_errors=True)
            use_env_proxy = idx == 1
            source_name = f"yuanbao-free-api#{idx}"
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
        _print_warn(f"  下载/解压 yuanbao-free-api 失败: {e}")
        return False
    finally:
        _rmtree_if_exists(temp_root, ignore_errors=True)


def ensure_yuanbao_service_bundle() -> bool:
    _print_stage(5, "准备 YuanBao-Free-API 本地中转服务...")
    bundle_ok = _download_yuanbao_service_bundle()
    if not bundle_ok:
        return False

    print("  元宝登录将直接复用系统已安装的 Edge / Chrome，不再额外部署内置 Chromium。")
    return bundle_ok


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
                sys.stdout.write("\r" + _render_transfer_progress("    downloading", current, total, start_time))
                sys.stdout.flush()
                last_draw = now

        sys.stdout.write("\r" + _render_transfer_progress("    downloading", current, total, start_time) + "\n")
        sys.stdout.flush()

    final_size = dest_path.stat().st_size if dest_path.exists() else 0
    if total and final_size != total:
        raise IOError(f"download incomplete: {final_size}/{total} bytes")


def _extract_zip_with_progress(zip_path, extract_root):
    _rmtree_if_exists(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        total = sum(max(0, item.file_size) for item in members if not item.is_dir())
        current = 0
        start_time = time.perf_counter()
        last_draw = 0.0

        for item in members:
            zf.extract(item, extract_root)
            if not item.is_dir():
                current += max(0, item.file_size)
            now = time.perf_counter()
            if now - last_draw >= 0.12:
                sys.stdout.write("\r" + _render_transfer_progress("    extracting ", current, total, start_time))
                sys.stdout.flush()
                last_draw = now

        sys.stdout.write("\r" + _render_transfer_progress("    extracting ", current, total, start_time) + "\n")
        sys.stdout.flush()


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

    for source in spec["urls"]:
        print(f"  - {spec['name']} ({label}, {source['name']})")
        try:
            _cleanup_vosk_temp_artifacts(archive_path, part_path, extract_root)
            _stream_download_with_progress(source["url"], part_path, label=source["name"])
            part_path.replace(archive_path)
            _extract_zip_with_progress(archive_path, extract_root)
            source_dir = _resolve_vosk_model_source_dir(extract_root)

            _rmtree_if_exists(target_dir)
            shutil.move(str(source_dir), str(target_dir))
            print(f"    model installed: {rel_target}")
            return True
        except (urllib.error.URLError, OSError, zipfile.BadZipFile, FileNotFoundError) as e:
            print(f"    failed: {e}")
        finally:
            _cleanup_vosk_temp_artifacts(archive_path, part_path, extract_root, ignore_errors=True)

    print(f"  warning: {label} model auto download failed")
    print("  manual download:")
    for source in spec["urls"]:
        print(f"    {source['url']}")
    print(f"  extract target: {rel_target}")
    return False


def ensure_vosk_models():
    _print_stage(4, "准备 Vosk 语音模型...")
    all_ok = True
    for spec in VOSK_MODEL_SPECS:
        if not _ensure_single_vosk_model(spec):
            all_ok = False
    return all_ok


def launch(python_exe):
    """Launch main script, prefer pythonw if available."""
    _print_stage(6, "启动飞行雪绒桌宠...")

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

        if _microphone_runtime_ready(python_exe):
            if not ensure_vosk_models():
                _print_warn("部分 Vosk 模型缺失，语音识别可能无法正常工作")
        else:
            _print_stage(4, "跳过 Vosk 模型下载（sounddevice/vosk 未就绪）")

        if not ensure_yuanbao_service_bundle():
            _print_warn("YuanBao-Free-API 本地中转未准备完成，元宝 web 模式可能不可用")


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
