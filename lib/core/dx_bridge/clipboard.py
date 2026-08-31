"""Qt-free Windows clipboard adapter for DirectX-owned UI."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


def write_clipboard_text(text: str) -> bool:
    value = str(text or "")
    if os.name != "nt" or not value:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    payload = (value + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(payload))
    if not handle:
        return False
    transferred = False
    try:
        address = kernel32.GlobalLock(handle)
        if not address:
            return False
        try:
            ctypes.memmove(address, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.OpenClipboard(None):
            return False
        try:
            if not user32.EmptyClipboard():
                return False
            transferred = bool(user32.SetClipboardData(_CF_UNICODETEXT, handle))
            return transferred
        finally:
            user32.CloseClipboard()
    finally:
        if not transferred:
            kernel32.GlobalFree(handle)


__all__ = ["write_clipboard_text"]
