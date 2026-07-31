"""Paths and version contract for the optional DirectML voice worker."""

from __future__ import annotations

import json
from pathlib import Path

from config.shared_storage_paths import get_shared_root_dir


DIRECTML_RUNTIME_VERSION = "1.22.0"
DIRECTML_RUNTIME_REQUIREMENT = f"onnxruntime-directml=={DIRECTML_RUNTIME_VERSION}"
DIRECTML_RUNTIME_ABI = "cp311-win_amd64"
DIRECTML_RUNTIME_MARKER_NAME = "runtime.json"


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
