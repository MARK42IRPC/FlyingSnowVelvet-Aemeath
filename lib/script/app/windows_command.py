"""Helpers for invoking Windows PowerShell without reparsing user paths."""

from __future__ import annotations

import base64
import os


def get_windows_powershell_executable() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    executable = os.path.join(
        system_root,
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    return executable if os.path.exists(executable) else "powershell.exe"


def encode_powershell_script(script: str) -> str:
    """Encode a script using the UTF-16LE format required by PowerShell 5.1."""
    return base64.b64encode(str(script).encode("utf-16-le")).decode("ascii")


def build_encoded_powershell_command(script: str) -> list[str]:
    """Build a command whose argv contains no user-controlled shell syntax."""
    return [
        get_windows_powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encode_powershell_script(script),
    ]
