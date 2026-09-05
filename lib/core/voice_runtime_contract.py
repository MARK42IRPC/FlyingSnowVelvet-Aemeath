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

CUDA_RUNTIME_VERSION = "1.22.0"
CUDA_RUNTIME_ABI = "cp311-win_amd64"
CUDA_RUNTIME_MARKER_NAME = "runtime.json"

# The optional Bundle is deliberately separate from the DirectML runtime above.
# Keep this list conservative: these are the DLLs observed while running every
# current voice graph with ORT 1.22/CUDA 12 on Windows.  The package builder
# and installer both consume this single source of truth.
CUDA_RUNTIME_BUNDLE_FORMAT = "aemeath-onnx-cuda-runtime"
CUDA_RUNTIME_BUNDLE_FORMAT_VERSION = 1
CUDA_RUNTIME_BUNDLE_RELEASE = "r1"
CUDA_RUNTIME_BUNDLE_ARCHIVE_NAME = (
    "aemeath-onnx-cuda-r1-ort1.22-cu12-cp311-win_amd64.zip"
)
CUDA_RUNTIME_BUNDLE_SHA256 = (
    "643225a1b6544315b6b3d0c41cc5ed65be15c5b1ea7fb33ee3295bc3d5d348b1"
)
CUDA_RUNTIME_BUNDLE_ARCHIVE_BYTES = 1_700_089_579
CUDA_RUNTIME_BUNDLE_PAYLOAD_BYTES = 2_532_836_762
CUDA_RUNTIME_BUNDLE_STAGING_OVERHEAD_BYTES = 512 * 1024 * 1024
CUDA_RUNTIME_BUNDLE_URLS = (
    "https://www.modelscope.cn/models/Mark42IRPC/GSV_onnx_Aemeath_Pack/resolve/master/"
    + CUDA_RUNTIME_BUNDLE_ARCHIVE_NAME,
    "https://huggingface.co/Mark42IRP/Aemeath_onnx_GSV_model/resolve/main/"
    + CUDA_RUNTIME_BUNDLE_ARCHIVE_NAME,
)
CUDA_RUNTIME_BUNDLE_PAYLOAD_ROOT = "payload"
CUDA_RUNTIME_BUNDLE_DLL_DIRECTORY = (
    "Lib/site-packages/aemeath_cuda_runtime/cuda/bin"
)
CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS = (
    "cublasLt64_12.dll",
    "cublas64_12.dll",
    "cufft64_11.dll",
    "cudart64_12.dll",
    "cudnn_engines_precompiled64_9.dll",
    "cudnn_adv64_9.dll",
    "cudnn_ops64_9.dll",
    "cudnn_heuristic64_9.dll",
    "cudnn_graph64_9.dll",
    "cudnn_engines_runtime_compiled64_9.dll",
    "cudnn_engines_tensor_ir64_9.dll",
    "cudnn64_9.dll",
)
CUDA_RUNTIME_BUNDLE_ORT_FILES = (
    "__init__.py",
    "capi/__init__.py",
    "capi/_ld_preload.py",
    "capi/_pybind_state.py",
    "capi/build_and_package_info.py",
    "capi/onnxruntime_inference_collection.py",
    "capi/onnxruntime_pybind11_state.pyd",
    "capi/onnxruntime_providers_cuda.dll",
    "capi/onnxruntime_providers_shared.dll",
    "capi/onnxruntime.dll",
    "capi/onnxruntime_validation.py",
    "capi/version_info.py",
    "LICENSE",
    "ThirdPartyNotices.txt",
)


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


def get_cuda_runtime_root() -> Path:
    return (
        get_shared_root_dir()
        / "voice"
        / "runtimes"
        / "onnx-cuda"
        / f"{CUDA_RUNTIME_VERSION}-{CUDA_RUNTIME_ABI}"
    )


def get_cuda_python_path(runtime_root: Path | None = None) -> Path:
    root = Path(runtime_root) if runtime_root is not None else get_cuda_runtime_root()
    return root / "Scripts" / "python.exe"


def get_cuda_runtime_marker_path(runtime_root: Path | None = None) -> Path:
    root = Path(runtime_root) if runtime_root is not None else get_cuda_runtime_root()
    return root / CUDA_RUNTIME_MARKER_NAME


def get_cuda_provider_dll_path(runtime_root: Path | None = None) -> Path:
    root = Path(runtime_root) if runtime_root is not None else get_cuda_runtime_root()
    return (
        root
        / "Lib"
        / "site-packages"
        / "onnxruntime"
        / "capi"
        / "onnxruntime_providers_cuda.dll"
    )


def get_cuda_bundle_dll_dir(runtime_root: Path | None = None) -> Path:
    """Return the flat CUDA DLL directory used by a downloaded bundle."""
    root = Path(runtime_root) if runtime_root is not None else get_cuda_runtime_root()
    return root / Path(CUDA_RUNTIME_BUNDLE_DLL_DIRECTORY)


def get_cuda_bundle_manifest_path(runtime_root: Path | None = None) -> Path:
    """Return the bundle manifest path inside an installed runtime."""
    root = Path(runtime_root) if runtime_root is not None else get_cuda_runtime_root()
    return root / "bundle.json"


def is_cuda_runtime_ready(runtime_root: Path | None = None) -> bool:
    """Check the pinned Bundle marker and its required runtime files."""
    root = Path(runtime_root) if runtime_root is not None else get_cuda_runtime_root()
    python_path = get_cuda_python_path(root)
    marker_path = get_cuda_runtime_marker_path(root)
    if not python_path.is_file() or not get_cuda_provider_dll_path(root).is_file():
        return False
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
        manifest = json.loads(get_cuda_bundle_manifest_path(root).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    ready = (
        isinstance(payload, dict)
        and isinstance(manifest, dict)
        and payload.get("runtime") == "onnxruntime-gpu"
        and payload.get("version") == CUDA_RUNTIME_VERSION
        and payload.get("abi") == CUDA_RUNTIME_ABI
        and payload.get("provider") == "CUDAExecutionProvider"
        and payload.get("source") == "bundle"
        and payload.get("bundle_format") == CUDA_RUNTIME_BUNDLE_FORMAT
        and payload.get("bundle_version") == CUDA_RUNTIME_BUNDLE_FORMAT_VERSION
        and payload.get("archive_sha256") == CUDA_RUNTIME_BUNDLE_SHA256
        and bool(payload.get("bundle_id"))
        and payload.get("bundle_id") == manifest.get("bundle_id")
        and manifest.get("format") == CUDA_RUNTIME_BUNDLE_FORMAT
        and manifest.get("format_version") == CUDA_RUNTIME_BUNDLE_FORMAT_VERSION
        and manifest.get("python_abi") == CUDA_RUNTIME_ABI
        and manifest.get("onnxruntime_version") == CUDA_RUNTIME_VERSION
    )
    if not ready:
        return False
    dll_dir = get_cuda_bundle_dll_dir(root)
    required = tuple(payload.get("required_dlls") or CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS)
    return (
        required == CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS
        and dll_dir.is_dir()
        and all((dll_dir / name).is_file() for name in required)
    )
