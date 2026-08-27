"""Resolve the default office workspace through the Windows Known Folder API."""

from __future__ import annotations

import ctypes
import os
import uuid
from pathlib import Path


_FOLDERID_DESKTOP = uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
DEFAULT_WORKSPACE_NAME = "飞行雪绒办公区"


class _Guid(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    )

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> "_Guid":
        raw = value.bytes_le
        return cls(
            int.from_bytes(raw[0:4], "little"),
            int.from_bytes(raw[4:6], "little"),
            int.from_bytes(raw[6:8], "little"),
            (ctypes.c_ubyte * 8).from_buffer_copy(raw[8:16]),
        )


def _windows_desktop_dir() -> Path | None:
    if os.name != "nt":
        return None
    pointer = ctypes.c_wchar_p()
    guid = _Guid.from_uuid(_FOLDERID_DESKTOP)
    try:
        result = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
            ctypes.byref(guid), 0, None, ctypes.byref(pointer)
        )
        if result != 0 or not pointer.value:
            return None
        return Path(pointer.value)
    except (AttributeError, OSError):
        return None
    finally:
        if pointer.value:
            try:
                ctypes.windll.ole32.CoTaskMemFree(pointer)  # type: ignore[attr-defined]
            except (AttributeError, OSError):
                pass


def resolve_desktop_dir() -> Path:
    known = _windows_desktop_dir()
    if known is not None:
        return known
    desktop = Path.home() / "Desktop"
    return desktop if desktop.exists() else Path.home()


def ensure_default_office_workspace() -> Path:
    workspace = resolve_desktop_dir() / DEFAULT_WORKSPACE_NAME
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace.resolve()
