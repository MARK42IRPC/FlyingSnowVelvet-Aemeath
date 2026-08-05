"""Paths and version contract for the optional DirectML voice worker."""

from lib.core.voice_runtime_contract import (
    DIRECTML_RUNTIME_ABI,
    DIRECTML_RUNTIME_MARKER_NAME,
    DIRECTML_RUNTIME_REQUIREMENT,
    DIRECTML_RUNTIME_VERSION,
    get_directml_dll_path,
    get_directml_python_path,
    get_directml_runtime_marker_path,
    get_directml_runtime_root,
    get_shared_root_dir,
    is_directml_runtime_ready,
)

__all__ = [
    "DIRECTML_RUNTIME_ABI",
    "DIRECTML_RUNTIME_MARKER_NAME",
    "DIRECTML_RUNTIME_REQUIREMENT",
    "DIRECTML_RUNTIME_VERSION",
    "get_directml_dll_path",
    "get_directml_python_path",
    "get_directml_runtime_marker_path",
    "get_directml_runtime_root",
    "get_shared_root_dir",
    "is_directml_runtime_ready",
]
