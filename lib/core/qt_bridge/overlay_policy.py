"""覆盖层窗口策略：激活抑制与清空后的隐藏去抖。

Qt 顶层覆盖层默认是可激活的：粒子/特效清空与重新出现时都会 ``hide()`` /
``show()``，前台窗口跟着在覆盖层和其它窗口之间来回移动，Windows 任务栏因此出现
类似焦点争夺的闪烁。DX 后端创建覆盖层窗口时固定使用
``FSDX_WINDOW_FLAG_NO_ACTIVATE``，Qt 侧此前没有对应策略；这里补齐，并把
"清空即隐藏"改成短暂滞留，降低原生 show/hide 与
``LayerManager.enforce_burst()`` 的频率。
"""
from __future__ import annotations

import sys

from config.config import PARTICLES

DEFAULT_HIDE_LINGER_MS = 500

_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WINDOW_LONG_APIS: tuple | None = None


def _get_window_long_apis() -> tuple:
    """返回 (读扩展样式, 写扩展样式, IsWindow)；非 Windows 返回 (None, None, None)。"""
    global _WINDOW_LONG_APIS
    if _WINDOW_LONG_APIS is not None:
        return _WINDOW_LONG_APIS
    apis: tuple = (None, None, None)
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        getter = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        getter.argtypes = (wintypes.HWND, ctypes.c_int)
        getter.restype = ctypes.c_ssize_t
        setter.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        setter.restype = ctypes.c_ssize_t
        user32.IsWindow.argtypes = (wintypes.HWND,)
        user32.IsWindow.restype = wintypes.BOOL
        apis = (getter, setter, user32.IsWindow)
    _WINDOW_LONG_APIS = apis
    return apis


def enable_no_activate(widget, *, window_long_apis: tuple | None = None) -> bool:
    """给 Qt 顶层窗口补上 WS_EX_NOACTIVATE，返回最终是否带该样式。

    ``Qt.WindowDoesNotAcceptFocus`` 在 Windows 平台上不会落到 WS_EX_NOACTIVATE，
    必须直接改扩展样式；非 Windows、离屏平台或句柄无效时安全返回 False。
    """
    getter, setter, is_window = (
        window_long_apis if window_long_apis is not None else _get_window_long_apis()
    )
    if getter is None or setter is None or is_window is None:
        return False
    try:
        handle = int(widget.winId())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    if not handle or not is_window(handle):
        return False
    try:
        style = int(getter(handle, _GWL_EXSTYLE)) & 0xFFFFFFFF
        if not style & _WS_EX_NOACTIVATE:
            setter(handle, _GWL_EXSTYLE, style | _WS_EX_NOACTIVATE)
        return bool(int(getter(handle, _GWL_EXSTYLE)) & _WS_EX_NOACTIVATE)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def resolve_hide_linger_ms(value=None) -> int:
    """解析覆盖层清空后的滞留隐藏时长；0 表示保留立即隐藏的旧行为。"""
    if value is None:
        value = PARTICLES.get("overlay_hide_linger_ms", DEFAULT_HIDE_LINGER_MS)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_HIDE_LINGER_MS


__all__ = ["DEFAULT_HIDE_LINGER_MS", "enable_no_activate", "resolve_hide_linger_ms"]
