"""Locate and launch the uninstaller of the desktop pet.

The offline distribution ships ``app\\卸载飞行雪绒.exe`` next to
``启动飞行雪绒.exe`` (see ``installer/windows/README.md``).  A source checkout
ships neither, and there "uninstall" simply means deleting the folder, so the
settings panel has to say that instead of pretending something can be run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib.script.app.launch_entry import PACKAGED_LAUNCHER_NAME, resolve_launch_entry
from lib.script.app.restart import _detached_kwargs

UNINSTALLER_FILE_NAME = "卸载飞行雪绒.exe"


def packaged_uninstaller_path(app_root: Path) -> Path:
    """``app\\卸载飞行雪绒.exe`` — where the offline installer drops it."""
    return Path(app_root) / UNINSTALLER_FILE_NAME


def resolve_uninstaller(app_root: Path) -> Path | None:
    """Return this layout's uninstaller, or ``None`` in a source checkout.

    The launcher decides: only a layout that ships the packaged
    ``启动飞行雪绒.exe`` was produced by the native installer and therefore
    has an install tree (plus an uninstaller) to hand over.  A checkout with
    just ``启动程序.bat`` keeps its sources and gets no uninstall target.
    """
    root = Path(app_root)
    launcher = resolve_launch_entry(root)
    if launcher is None or launcher.name != PACKAGED_LAUNCHER_NAME:
        return None
    uninstaller = packaged_uninstaller_path(root)
    return uninstaller if uninstaller.is_file() else None


def uninstaller_launch_command(uninstaller_path: Path) -> list[str]:
    """The uninstaller takes no arguments and resolves its own install root."""
    return [str(Path(uninstaller_path).resolve())]


def launch_uninstaller(uninstaller_path: Path) -> subprocess.Popen:
    """Start the uninstaller detached so it outlives the pet that spawned it.

    The pet exits right after this call, and the uninstaller has to keep
    running inside the same process group: ``_detached_kwargs`` mirrors what
    ``update_installer.launch_update_installer`` uses for the same reason.
    """
    command = uninstaller_launch_command(uninstaller_path)
    try:
        return subprocess.Popen(command, **_detached_kwargs())
    except OSError:
        # Some Windows jobs forbid breakaway; a plain detached launch is enough.
        return subprocess.Popen(command, **_detached_kwargs(include_breakaway=False))


__all__ = [
    "UNINSTALLER_FILE_NAME",
    "launch_uninstaller",
    "packaged_uninstaller_path",
    "resolve_uninstaller",
    "uninstaller_launch_command",
]