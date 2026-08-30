"""Python discovery, pip bootstrap, and launcher configuration."""

import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from .catalog import (
    GET_PIP_DOWNLOAD_TIMEOUT,
    GET_PIP_URLS,
    MIN_VERSION,
    PROJECT_ROOT,
    TARGET_PYTHON,
)
from .console import _print_info, _print_kind, _print_stage


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
        return text.encode(encoding, errors="replace").decode(
            encoding,
            errors="replace",
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

def _download_get_pip(destination: Path) -> str:
    part_path = destination.with_name(destination.name + ".part")
    last_error = "没有可用下载源"
    for url in GET_PIP_URLS:
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "FlyingSnowVelvetInstaller/1.0"},
            )
            with urllib.request.urlopen(
                request,
                timeout=GET_PIP_DOWNLOAD_TIMEOUT,
            ) as response, part_path.open("wb") as output:
                shutil.copyfileobj(response, output, length=256 * 1024)
            if part_path.stat().st_size <= 0:
                raise OSError("下载结果为空")
            part_path.replace(destination)
            return url
        except Exception as exc:
            last_error = f"{url}: {exc}"
            try:
                part_path.unlink()
            except OSError:
                pass
    raise OSError(last_error)

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
        source = _download_get_pip(tmp)
        print(f"  get-pip.py 下载源: {source}")
        r = _run([python_exe, str(tmp)], timeout=240)
        if r and r.returncode == 0 and _has_pip(python_exe):
            _print_kind("  已通过 get-pip.py 安装 pip", "ok", prefix=False)
            return True
    except Exception as e:
        _print_kind(f"  get-pip.py 执行失败: {e}", "warn", prefix=False)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

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


__all__ = (
    '_console_safe',
    '_run',
    '_python_module_cmd',
    '_run_python_module',
    '_run_pip',
    '_UV_PYVENV_PATTERN',
    '_UV_MANAGED_TEXT_PATTERN',
    '_read_environment_marker',
    '_python_environment_roots',
    '_is_uv_managed_python',
    '_discover_all_pythons',
    '_probe_python_info',
    '_current_runtime_executable',
    '_get_version',
    '_has_pip',
    '_fmt_ver',
    '_download_get_pip',
    '_sort_key',
    '_fallback_python_selection',
    '_select_ranked_python',
    'select_best_python',
    'ensure_pip',
    '_resolve_pythonw_path',
    '_to_short_windows_path',
    '_to_env_macro_path',
    '_to_batch_safe_path',
    'save_config',
    '_SPEC_ONLY_IMPORT_PREFIX',
    '_pkg_installed',
)
