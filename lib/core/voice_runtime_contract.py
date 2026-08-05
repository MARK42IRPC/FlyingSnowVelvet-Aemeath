"""Standard-library-only contract for the optional DirectML voice runtime.

The dependency installer imports this module before third-party packages are
available.  Keep it independent from the application configuration package.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


DIRECTML_RUNTIME_VERSION = "1.22.0"
DIRECTML_RUNTIME_REQUIREMENT = f"onnxruntime-directml=={DIRECTML_RUNTIME_VERSION}"
DIRECTML_RUNTIME_ABI = "cp311-win_amd64"
DIRECTML_RUNTIME_MARKER_NAME = "runtime.json"


def get_shared_root_dir() -> Path:
    """Return the shared application root without importing ``config``."""
    override = str(os.environ.get("AEMEATH_DESK_PET_HOME", "") or "").strip()
    if override:
        return Path(override).expanduser()

    drive = str(os.environ.get("SystemDrive", "C:") or "C:").strip()
    drive = drive.rstrip("\\/") or "C:"
    if not drive.endswith(":"):
        drive = f"{drive}:"
    return Path(f"{drive}\\AemeathDeskPet")


def get_directml_runtime_root() -> Path:
    return (
        get_shared_root_dir()
        / "voice"
        / "runtimes"
        / "onnx-directml"
        / f"{DIRECTML_RUNTIME_VERSION}-{DIRECTML_RUNTIME_ABI}"
    )


def get_directml_python_path() -> Path:
    return get_directml_runtime_root() / "Scripts" / "python.exe"


def get_directml_runtime_marker_path() -> Path:
    return get_directml_runtime_root() / DIRECTML_RUNTIME_MARKER_NAME


def get_directml_dll_path() -> Path:
    return (
        get_directml_runtime_root()
        / "Lib"
        / "site-packages"
        / "onnxruntime"
        / "capi"
        / "DirectML.dll"
    )


def is_directml_runtime_ready() -> bool:
    """Check the executable, DirectML DLL and versioned runtime marker."""
    python_path = get_directml_python_path()
    marker_path = get_directml_runtime_marker_path()
    if not python_path.is_file() or not get_directml_dll_path().is_file():
        return False
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("runtime") == "onnxruntime-directml"
        and payload.get("version") == DIRECTML_RUNTIME_VERSION
        and payload.get("abi") == DIRECTML_RUNTIME_ABI
    )
