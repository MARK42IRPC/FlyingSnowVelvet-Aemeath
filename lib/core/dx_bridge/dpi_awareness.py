"""Win32 DPI-awareness setup required before creating DirectX windows."""
from __future__ import annotations

import ctypes
import os


_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_DPI_AWARENESS_PER_MONITOR_AWARE = 2


def _pseudo_handle(value: int) -> ctypes.c_void_p:
    bits = ctypes.sizeof(ctypes.c_void_p) * 8
    return ctypes.c_void_p(value & ((1 << bits) - 1))


def ensure_per_monitor_v2_dpi_awareness(user32=None) -> bool:
    """Enable the same per-monitor awareness Qt establishes on Windows."""
    if user32 is None:
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            return True
        user32 = ctypes.WinDLL("user32", use_last_error=True)

    get_thread_context = user32.GetThreadDpiAwarenessContext
    get_thread_context.argtypes = []
    get_thread_context.restype = ctypes.c_void_p
    contexts_equal = user32.AreDpiAwarenessContextsEqual
    contexts_equal.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    contexts_equal.restype = ctypes.c_bool
    get_awareness = user32.GetAwarenessFromDpiAwarenessContext
    get_awareness.argtypes = [ctypes.c_void_p]
    get_awareness.restype = ctypes.c_int
    set_process_context = user32.SetProcessDpiAwarenessContext
    set_process_context.argtypes = [ctypes.c_void_p]
    set_process_context.restype = ctypes.c_bool
    set_thread_context = user32.SetThreadDpiAwarenessContext
    set_thread_context.argtypes = [ctypes.c_void_p]
    set_thread_context.restype = ctypes.c_void_p

    target = _pseudo_handle(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    current = get_thread_context()
    if current and contexts_equal(current, target):
        return True
    if set_process_context(target):
        return True

    # A manifest or an earlier GUI initializer may have locked the process
    # context. The owner thread can still use PMv2 when Windows permits it.
    previous = set_thread_context(target)
    current = get_thread_context()
    if current and contexts_equal(current, target):
        return True
    if previous:
        set_thread_context(previous)
    return bool(
        current
        and get_awareness(current) == _DPI_AWARENESS_PER_MONITOR_AWARE
    )


__all__ = ["ensure_per_monitor_v2_dpi_awareness"]
