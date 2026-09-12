"""Helpers for starting helper processes without flashing a console."""

from __future__ import annotations

import os
import subprocess


def hidden_process_kwargs() -> dict[str, object]:
    """Return subprocess kwargs that keep helper consoles from flashing.

    Helper processes launched by the desktop pet (PowerShell probes, shortcut
    synchronization, in-app command execution) must never create a visible
    console window.  ``CREATE_NO_WINDOW`` suppresses the console for console
    hosts such as ``cmd.exe`` and ``powershell.exe``, while
    ``STARTF_USESHOWWINDOW``/``SW_HIDE`` covers GUI hosts that would otherwise
    honor the default ``SW_SHOWNORMAL``.  On non-Windows platforms no console
    is created for these helpers, so the result is empty.
    """
    if os.name != "nt":
        return {}
    kwargs: dict[str, object] = {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window
    startupinfo_class = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_class is not None:
        startupinfo = startupinfo_class()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs


__all__ = ["hidden_process_kwargs"]
