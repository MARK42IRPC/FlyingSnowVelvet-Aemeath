"""进入更新流程前解除桌宠自身对安装目录的占用。

Windows 上被加载的 EXE/DLL 与 Qt 注册过的字体会一直握到进程退出，更新器因此既覆盖不了
``runtime/python311`` 里的解释器，也覆盖不了办公侧车用的 ``node.exe`` 与它加载的 sharp /
cofork 原生模块（覆盖时报 13 号错误）。这里在覆盖安装或交接安装器之前，先结束所有镜像落在
安装目录里的自有子进程：办公 DSH 侧车、控制面板 helper、语音 worker 都在其中，进程一退，
它们占用的文件和 socket 一起释放。

只结束镜像确实位于安装目录内的进程，且永远跳过当前进程及其全部祖先进程——安装后的启动壳
``启动飞行雪绒.exe`` 是当前进程的父进程，用 ``taskkill /T`` 结束它会把桌宠自己一起带走。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Iterable

from lib.core.logger import get_logger
from lib.core.process_utils import hidden_process_kwargs

logger = get_logger(__name__)

_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_IMAGE_PATH_LENGTH = 32768
# ``taskkill /T`` 结束一棵进程树通常在一秒内返回；给足余量后仍不返回就继续走下面的流程，
# 不能让一次更新卡在等待子进程退出上。
_TASKKILL_TIMEOUT_SECONDS = 15.0
# 结束进程后给文件系统一点时间真正关掉句柄，再开始覆盖安装。
_SETTLE_SECONDS = 0.3


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _kernel32() -> ctypes.WinDLL:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _snapshot_processes() -> list[tuple[int, int, str]]:
    """返回当前机器的 ``(pid, 父 pid, 可执行文件名)`` 列表。"""
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
        return []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        processes: list[tuple[int, int, str]] = []
        while True:
            processes.append(
                (
                    int(entry.th32ProcessID),
                    int(entry.th32ParentProcessID),
                    str(entry.szExeFile or ""),
                )
            )
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return processes
    finally:
        kernel32.CloseHandle(snapshot)


def _process_image_path(pid: int) -> str:
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(_IMAGE_PATH_LENGTH)
        size = wintypes.DWORD(_IMAGE_PATH_LENGTH)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _ancestor_pids(processes: Iterable[tuple[int, int, str]]) -> set[int]:
    """当前进程的全部祖先 pid：它们要么正在托管桌宠，要么很快就会退出。"""
    parents = {pid: parent for pid, parent, _ in processes}
    ancestors: set[int] = set()
    own_pid = os.getpid()
    current = own_pid
    while True:
        parent = parents.get(current, 0)
        if not parent or parent == own_pid or parent in ancestors:
            return ancestors
        ancestors.add(parent)
        current = parent


def _is_inside(path: str, root: str) -> bool:
    if not path:
        return False
    candidate = os.path.normcase(os.path.abspath(path))
    prefix = os.path.normcase(os.path.abspath(root)).rstrip(os.sep) + os.sep
    return candidate.startswith(prefix)


def _workbench_helper_process_id() -> int | None:
    try:
        from lib.script.app.workbench_helper import workbench_helper_process_id

        return workbench_helper_process_id()
    except Exception:
        return None


def _restore_workbench_helper() -> None:
    """控制面板窗口被这次释放关掉时把它按原页面重新拉起来。"""
    try:
        from lib.script.app.workbench_helper import relaunch_workbench_helper

        relaunch_workbench_helper()
    except Exception as exc:
        logger.debug("重新拉起控制面板 helper 失败: %s", exc)


def _target_processes(
    processes: Iterable[tuple[int, int, str]], root: Path, protected: set[int]
) -> list[tuple[int, str]]:
    """挑出镜像位于安装目录内、且不是当前进程及祖先进程的目标。"""
    targets: list[tuple[int, str]] = []
    for pid, _, name in processes:
        if pid in protected:
            continue
        if not _is_inside(_process_image_path(pid), str(root)):
            continue
        targets.append((pid, name))
    return targets


def _terminate_process(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TASKKILL_TIMEOUT_SECONDS,
            check=False,
            **hidden_process_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("结束更新占用进程 %s 失败: %s", pid, exc)


def release_install_directory_locks(
    install_root: Path | str | None = None,
    *,
    info: Callable[[str], None] | None = None,
) -> tuple[str, ...]:
    """结束镜像位于安装目录内的自有子进程，返回被结束的进程描述。

    返回空元组表示没有需要释放的占用。失败不会抛异常：更新流程本身远比“清理干净”
    重要，剩下的占用交给覆盖安装的重试与延迟替换处理。
    """

    def notify(message: str) -> None:
        if callable(info):
            try:
                info(message)
            except Exception:
                pass

    if os.name != "nt":
        return ()
    try:
        root = (
            Path(install_root).resolve()
            if install_root is not None
            else Path(__file__).resolve().parents[3]
        )
        processes = _snapshot_processes()
    except Exception as exc:
        logger.debug("枚举更新占用进程失败: %s", exc)
        return ()
    protected = _ancestor_pids(processes) | {os.getpid()}
    targets = _target_processes(processes, root, protected)
    if not targets:
        return ()
    helper_pid = _workbench_helper_process_id()
    killed: list[str] = []
    names: list[str] = []
    killed_pids: set[int] = set()
    for pid, name in targets:
        _terminate_process(pid)
        killed_pids.add(pid)
        killed.append(f"{name or '未知进程'}({pid})")
        if name and name not in names:
            names.append(name)
    preview = "、".join(names[:3])
    notify(f"已结束 {len(killed)} 个占用安装目录的后台进程（{preview}）")
    logger.info("更新前释放安装目录占用：%s", "、".join(killed))
    time.sleep(_SETTLE_SECONDS)
    if helper_pid is not None and helper_pid in killed_pids:
        # 控制面板是用户看得见的窗口，被这次释放关掉就按原页面重新打开。
        _restore_workbench_helper()
    return tuple(killed)


__all__ = ["release_install_directory_locks"]
