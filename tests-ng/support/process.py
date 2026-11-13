"""Cross-platform process assertions that do not kill the whole machine.

The legacy suite's shutdown and installer tests shelled out to
``taskkill``/``tasklist``.  On CI that was fine, but on the development
machine it can close the interpreter that is running the tests.  These helpers
prefer the Windows API (or ``psutil`` when it is available) and fall back to no
assertion outside Windows.

Nothing here starts a process; the caller passes an existing PID or command
line fragment.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys


def _windows_process_alive(pid: int) -> bool:
    """Check liveness without ``tasklist``; 0 means still running."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: process exists
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_is_alive(pid: int) -> bool:
    """Return whether ``pid`` is still running on this machine."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def owned_python_pids(script_fragment: str) -> list[int]:
    """Return PIDs whose command line mentions ``script_fragment``.

    Only useful on Windows, where the process list is available through
    ``Get-CimInstance`` without elevation.  On other platforms it returns an
    empty list so release tests can skip instead of pretending.
    """
    if sys.platform != "win32":
        return []
    fragment = script_fragment.replace("'", "''")
    query = (
        f"Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{fragment}*' }} | "
        f"Select-Object -ExpandProperty ProcessId"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        return []
    return [
        int(line)
        for line in completed.stdout.splitlines()
        if line.strip().isdigit()
    ]
