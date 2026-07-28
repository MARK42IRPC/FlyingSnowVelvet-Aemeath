"""应用完成正常退出后使用的进程重启工具。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _write_restart_error(stage: str, exc: BaseException) -> None:
    try:
        log_dir = _project_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "restart.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {stage}: {exc}\n")
    except Exception:
        pass


def _write_restart_trace(message: str) -> None:
    try:
        log_dir = _project_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "restart.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def build_restart_command(
    argv: Sequence[str] | None = None,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    """构造与当前发行形态匹配的重启命令。"""
    current_argv = list(sys.argv if argv is None else argv)
    arguments = current_argv[1:]
    python_executable = executable or sys.executable
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if is_frozen:
        return [python_executable, *arguments]
    return [
        python_executable,
        str(_project_root() / "lib" / "core" / "qt_desktop_pet.py"),
        *arguments,
    ]


def _detached_kwargs(*, include_breakaway: bool = True) -> dict:
    kwargs = {
        "cwd": str(_project_root()),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
        if include_breakaway:
            flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            kernel32.GetExitCodeProcess.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int

            handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
            if not handle:
                return False
            try:
                exit_code = ctypes.c_uint32()
                queried = bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
                return queried and exit_code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def run_restart_helper(parent_pid: int, command: Sequence[str], max_wait: float = 30.0) -> int:
    """等待旧实例退出后启动新实例；供入口脚本的独立 helper 调用。"""
    _write_restart_trace(f"helper pid={os.getpid()} waiting parent pid={parent_pid}")
    deadline = time.monotonic() + max(0.0, float(max_wait))
    while _is_process_running(int(parent_pid)) and time.monotonic() < deadline:
        time.sleep(0.15)

    try:
        try:
            process = subprocess.Popen(list(command), **_detached_kwargs())
        except OSError:
            # 某些 Windows Job 不允许 breakaway，至少退回普通 detached 启动。
            process = subprocess.Popen(list(command), **_detached_kwargs(include_breakaway=False))
        _write_restart_trace(f"helper launched application pid={process.pid}")
        try:
            exit_code = process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            _write_restart_trace(f"application pid={process.pid} remained alive after startup probe")
            return 0
        _write_restart_trace(f"application pid={process.pid} exited early code={exit_code}")
        return 1
    except Exception as exc:
        _write_restart_error("helper launch application failed", exc)
        return 1


def launch_current_application() -> subprocess.Popen:
    """启动独立重启 helper，等待当前进程退出后再拉起桌宠。"""
    command = build_restart_command()
    helper_args = [
        "--fsv-restart-helper", str(os.getpid()), json.dumps(command, ensure_ascii=False)
    ]
    if bool(getattr(sys, "frozen", False)):
        helper_command = [sys.executable, *helper_args]
    else:
        helper_command = [
            sys.executable,
            str(_project_root() / "lib" / "core" / "qt_desktop_pet.py"),
            *helper_args,
        ]
    try:
        process = subprocess.Popen(helper_command, **_detached_kwargs())
    except OSError:
        # 当前进程不允许脱离 Job 时仍尝试普通 detached 启动。
        try:
            process = subprocess.Popen(helper_command, **_detached_kwargs(include_breakaway=False))
        except Exception as exc:
            _write_restart_error("launch helper failed", exc)
            raise
    _write_restart_trace(f"application pid={os.getpid()} launched helper pid={process.pid}")
    return process
