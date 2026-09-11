"""Standard-library-only contract for isolated DirectML/CUDA voice runtimes.

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
DIRECTML_BUNDLED_FORMAT = "fsv-bundled-directml-overlay"
DIRECTML_BUNDLED_FORMAT_VERSION = 1

# The self-written CUDA runtime is a single DLL that needs nothing but the
# NVIDIA display driver.  It ships with the release, so unlike DirectML there is
# no versioned virtual environment to install.
CUDA_VOICE_RUNTIME_DLL_NAME = "fsv_cuda_voice_runtime.dll"
CUDA_VOICE_RUNTIME_DIR_NAME = "cuda-voice"
CUDA_VOICE_RUNTIME_ENV_VAR = "AEMEATH_CUDA_VOICE_RUNTIME"

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


def _directml_marker_is_valid(marker_path: Path, *, bundled: bool) -> bool:
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    common = (
        payload.get("runtime") == "onnxruntime-directml"
        and payload.get("version") == DIRECTML_RUNTIME_VERSION
        and payload.get("abi") == DIRECTML_RUNTIME_ABI
    )
    if not bundled:
        return common
    return (
        common
        and payload.get("format") == DIRECTML_BUNDLED_FORMAT
        and payload.get("format_version") == DIRECTML_BUNDLED_FORMAT_VERSION
        and payload.get("provider") == "DmlExecutionProvider"
    )


def is_external_directml_runtime_ready() -> bool:
    """Check the optional user-managed DirectML virtual environment."""
    python_path = get_directml_python_path()
    marker_path = get_directml_runtime_marker_path()
    if not python_path.is_file() or not get_directml_dll_path().is_file():
        return False
    return _directml_marker_is_valid(marker_path, bundled=False)


def get_bundled_directml_runtime_root(app_root: Path | None = None) -> Path:
    root = Path(app_root) if app_root is not None else Path(__file__).resolve().parents[2]
    return (
        root.parent
        / "runtime"
        / "onnx-directml"
        / f"{DIRECTML_RUNTIME_VERSION}-{DIRECTML_RUNTIME_ABI}"
    )


def get_bundled_directml_site_packages(app_root: Path | None = None) -> Path:
    return get_bundled_directml_runtime_root(app_root) / "Lib" / "site-packages"


def get_bundled_directml_dll_path(app_root: Path | None = None) -> Path:
    return (
        get_bundled_directml_site_packages(app_root)
        / "onnxruntime"
        / "capi"
        / "DirectML.dll"
    )


def get_bundled_python_path(app_root: Path | None = None) -> Path:
    root = Path(app_root) if app_root is not None else Path(__file__).resolve().parents[2]
    return root.parent / "runtime" / "python311" / "python.exe"


def is_bundled_directml_runtime_ready(app_root: Path | None = None) -> bool:
    runtime_root = get_bundled_directml_runtime_root(app_root)
    return (
        get_bundled_python_path(app_root).is_file()
        and get_bundled_directml_dll_path(app_root).is_file()
        and _directml_marker_is_valid(
            runtime_root / DIRECTML_RUNTIME_MARKER_NAME,
            bundled=True,
        )
    )


def is_directml_runtime_ready() -> bool:
    """Prefer the release's read-only overlay, then the optional user runtime."""
    return is_bundled_directml_runtime_ready() or is_external_directml_runtime_ready()


def get_directml_worker_python_path() -> Path:
    if is_bundled_directml_runtime_ready():
        return get_bundled_python_path()
    return get_directml_python_path()


def get_directml_worker_site_packages() -> Path | None:
    if is_bundled_directml_runtime_ready():
        return get_bundled_directml_site_packages()
    return None


def get_cuda_voice_runtime_shared_root() -> Path:
    """User-visible home of the self-written CUDA runtime artifact."""

    return get_shared_root_dir() / "voice" / "runtimes" / CUDA_VOICE_RUNTIME_DIR_NAME


def get_shared_cuda_voice_runtime_path() -> Path:
    return get_cuda_voice_runtime_shared_root() / CUDA_VOICE_RUNTIME_DLL_NAME


def get_bundled_cuda_voice_runtime_path(app_root: Path | None = None) -> Path:
    """Runtime that ships next to the executable inside a release."""

    root = Path(app_root) if app_root is not None else Path(__file__).resolve().parents[2]
    return root.parent / "runtime" / CUDA_VOICE_RUNTIME_DIR_NAME / CUDA_VOICE_RUNTIME_DLL_NAME


def get_development_cuda_voice_runtime_path(app_root: Path | None = None) -> Path:
    """Build output of ``native/cuda_voice_runtime`` on a development machine."""

    root = Path(app_root) if app_root is not None else Path(__file__).resolve().parents[2]
    return (
        root
        / "build"
        / "cuda_voice_runtime"
        / "Release"
        / CUDA_VOICE_RUNTIME_DLL_NAME
    )


def get_cuda_voice_runtime_candidates(app_root: Path | None = None) -> tuple[Path, ...]:
    """Every location that may hold the runtime, most authoritative first."""

    override = str(os.environ.get(CUDA_VOICE_RUNTIME_ENV_VAR, "") or "").strip()
    candidates: list[Path] = []
    if override:
        candidate = Path(override).expanduser()
        candidates.append(candidate / CUDA_VOICE_RUNTIME_DLL_NAME if candidate.is_dir() else candidate)
    candidates.extend(
        (
            get_bundled_cuda_voice_runtime_path(app_root),
            get_development_cuda_voice_runtime_path(app_root),
            get_shared_cuda_voice_runtime_path(),
        )
    )
    return tuple(candidates)


def resolve_cuda_voice_runtime_path(app_root: Path | None = None) -> Path | None:
    """Return the first existing runtime DLL, or ``None`` when unavailable."""

    for candidate in get_cuda_voice_runtime_candidates(app_root):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None
