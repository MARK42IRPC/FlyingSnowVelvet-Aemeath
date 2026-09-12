"""Resolve the entry point used to start the desktop pet.

Offline distributions ship a native ``启动飞行雪绒.exe`` launcher and no longer
carry ``启动程序.bat``.  Source checkouts keep using the batch entry so that
``py.ini`` and the developer Python interpreter stay authoritative.  Shortcut
and autostart code must never hard-code one of the two forms.
"""

from __future__ import annotations

from pathlib import Path

PACKAGED_LAUNCHER_NAME = "启动飞行雪绒.exe"
SOURCE_LAUNCH_SCRIPT_NAME = "启动程序.bat"


def packaged_launcher_path(root: Path) -> Path:
    return Path(root) / PACKAGED_LAUNCHER_NAME


def source_launch_script_path(root: Path) -> Path:
    return Path(root) / SOURCE_LAUNCH_SCRIPT_NAME


def resolve_launch_entry(root: Path) -> Path | None:
    """Return the preferred launch entry, or ``None`` when neither exists."""
    root = Path(root)
    packaged = packaged_launcher_path(root)
    if packaged.is_file():
        return packaged
    source = source_launch_script_path(root)
    if source.is_file():
        return source
    return None


__all__ = [
    "PACKAGED_LAUNCHER_NAME",
    "SOURCE_LAUNCH_SCRIPT_NAME",
    "packaged_launcher_path",
    "resolve_launch_entry",
    "source_launch_script_path",
]
