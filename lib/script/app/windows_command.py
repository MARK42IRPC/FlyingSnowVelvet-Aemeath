"""Helpers for invoking Windows PowerShell without reparsing user paths."""

from __future__ import annotations

import base64
import os
from pathlib import Path


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


def build_bat_command(project_root: Path, mode: str = "normal") -> list[str]:
    """Build a detached-safe command for a repository batch entry point.

    The batch path is transported inside the encoded PowerShell payload so
    characters such as ``&``, ``!`` and non-ASCII names are never reparsed by
    ``cmd.exe`` or PowerShell argument handling.
    """
    bat_name = "安装依赖.bat" if str(mode).strip().lower() in {"environment", "env"} else "启动程序.bat"
    bat_path = Path(project_root).resolve() / bat_name
    if not bat_path.is_file():
        raise FileNotFoundError(f"启动入口不存在：{bat_path}")
    encoded_path = base64.b64encode(str(bat_path).encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$batPath = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_path}'))\n"
        "& $batPath\n"
        "exit $LASTEXITCODE\n"
    )
    return build_encoded_powershell_command(script)
