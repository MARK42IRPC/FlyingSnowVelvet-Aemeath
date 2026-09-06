"""Self-contained NVIDIA voice runtime bundle (v2).

The existing CUDA bundle contract was designed around a target-machine
Python virtual environment.  This module describes the next release shape:
the archive carries its own CPython, ORT CUDA package, CUDA/cuDNN DLLs and a
worker launcher.  The NVIDIA display driver is the only host prerequisite.

Only the Python standard library is used here.  That is intentional: archive
inspection and offline verification must work before the application runtime
or any third-party package has been imported.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping, Sequence

from . import voice_runtime_contract as _legacy_contract


class CudaRuntimeV2Error(RuntimeError):
    """Raised when a v2 runtime bundle is malformed or unverifiable."""


CUDA_RUNTIME_V2_FORMAT = "fsv-cuda-runtime"
CUDA_RUNTIME_V2_FORMAT_VERSION = 2
CUDA_RUNTIME_V2_RELEASE = "r2"
CUDA_RUNTIME_V2_PLATFORM = "windows"
CUDA_RUNTIME_V2_ARCHITECTURE = "amd64"
CUDA_RUNTIME_V2_PYTHON_ABI = "cp311-win_amd64"
CUDA_RUNTIME_V2_PYTHON_VERSION = "3.11"
CUDA_RUNTIME_V2_ORT_VERSION = "1.22.0"
CUDA_RUNTIME_V2_CUDA_MAJOR = 12
CUDA_RUNTIME_V2_CUDNN_MAJOR = 9
CUDA_RUNTIME_V2_WORKER_PROTOCOL = "stdio-v1"
CUDA_RUNTIME_V2_PAYLOAD_ROOT = "payload"
CUDA_RUNTIME_V2_MANIFEST_NAME = "manifest.json"
CUDA_RUNTIME_V2_CHECKSUM_NAME = "SHA256SUMS.txt"
CUDA_RUNTIME_V2_RELEASE_SUFFIX = ".release.json"

# Paths are relative to the archive's payload directory.  A full CPython tree
# is deliberate; no system Python, pip, or virtualenv is consulted at launch.
CUDA_RUNTIME_V2_PYTHON_EXECUTABLE = "python/python.exe"
CUDA_RUNTIME_V2_PYTHON_DLL = "python/python311.dll"
CUDA_RUNTIME_V2_RUNTIME_SITE_PACKAGES = "runtime/Lib/site-packages"
CUDA_RUNTIME_V2_ORT_ROOT = "runtime/Lib/site-packages/onnxruntime"
CUDA_RUNTIME_V2_ORT_PROVIDER = (
    "runtime/Lib/site-packages/onnxruntime/capi/onnxruntime_providers_cuda.dll"
)
CUDA_RUNTIME_V2_ORT_CORE = "runtime/Lib/site-packages/onnxruntime/capi/onnxruntime.dll"
CUDA_RUNTIME_V2_ORT_FILES = tuple(
    f"runtime/Lib/site-packages/onnxruntime/{relative}"
    for relative in _legacy_contract.CUDA_RUNTIME_BUNDLE_ORT_FILES
)
CUDA_RUNTIME_V2_CUDA_DLL_DIRECTORY = "runtime/cuda/bin"
CUDA_RUNTIME_V2_WORKER_ENTRY = "worker/cuda_worker.py"
CUDA_RUNTIME_V2_WORKER_LAUNCHER = "worker/launch_cuda_worker.cmd"
CUDA_RUNTIME_V2_WORKER_PYTHON_LAUNCHER = "worker/launch_cuda_worker.py"

# Keep the native list aligned with the already validated r1 package while
# moving it into the self-contained payload.  The v2 manifest records it, so a
# future CUDA/cuDNN refresh becomes a new release instead of an implicit change.
CUDA_RUNTIME_V2_REQUIRED_DLLS = tuple(
    _legacy_contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS
)

# The minimum driver is a release policy, not a CUDA toolkit dependency.  It
# can be raised for a future bundle without changing the verifier API.  The
# value follows the Windows NVIDIA driver branch used by CUDA 12.x; consumers
# should still allow a release manifest to override it.
CUDA_RUNTIME_V2_MIN_DRIVER_VERSION = "531.61"

_HASH_CHUNK_SIZE = 1024 * 1024
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 16 * 1024 * 1024 * 1024
_MAX_MANIFEST_FILES = 200_000
ProgressCallback = Callable[[int, int], None]
CancellationCheck = Callable[[], None]
DriverProbe = Callable[[], str | None]


@dataclass(frozen=True)
class RuntimeFile:
    """One payload file and its integrity metadata."""

    path: str
    role: str
    size: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "role": self.role,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class OfflineVerification:
    """Result returned by :func:`verify_archive_offline`."""

    archive: Path
    archive_bytes: int
    archive_sha256: str
    bundle_id: str
    file_count: int
    payload_bytes: int
    driver_requirement: str
    detected_driver: str | None
    worker_command: tuple[str, ...]


def _safe_relative_name(value: object) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text or "\x00" in text:
        raise CudaRuntimeV2Error(f"unsafe bundle path: {value!r}")
    # A drive prefix is absolute even when PurePosixPath does not consider it
    # so on POSIX test hosts.
    if text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        raise CudaRuntimeV2Error(f"bundle path is absolute: {value!r}")
    raw_parts = text.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise CudaRuntimeV2Error(f"bundle path is unsafe: {value!r}")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CudaRuntimeV2Error(f"bundle path is unsafe: {value!r}")
    return path.as_posix()


def _relative_path(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise CudaRuntimeV2Error(f"payload file escapes source root: {path}") from exc
    return _safe_relative_name(relative.as_posix())


def sha256_file(
    path: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> str:
    """Hash a file without loading it into memory."""
    digest = hashlib.sha256()
    source = Path(path)
    try:
        total = max(0, source.stat().st_size)
        stream = source.open("rb")
    except OSError as exc:
        raise CudaRuntimeV2Error(f"cannot read payload file: {source}") from exc
    current = 0
    with stream:
        while True:
            if cancellation_check is not None:
                cancellation_check()
            chunk = stream.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            current += len(chunk)
            if progress_callback is not None:
                progress_callback(current, total)
    return digest.hexdigest()


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")


def _driver_tuple(value: str) -> tuple[int, ...]:
    text = str(value or "").strip()
    if not text:
        raise CudaRuntimeV2Error("NVIDIA driver version is empty")
    pieces = text.split(".")
    if any(not piece.isdigit() for piece in pieces):
        raise CudaRuntimeV2Error(f"invalid NVIDIA driver version: {value!r}")
    return tuple(int(piece) for piece in pieces)


def driver_version_satisfies(version: str, minimum: str) -> bool:
    """Compare dotted NVIDIA driver versions without contacting the network."""
    actual = _driver_tuple(version)
    expected = _driver_tuple(minimum)
    width = max(len(actual), len(expected))
    return actual + (0,) * (width - len(actual)) >= expected + (0,) * (width - len(expected))


def _classify_role(relative: str) -> str:
    lowered = relative.lower()
    if lowered.startswith("python/"):
        return "cpython"
    if lowered.startswith("runtime/lib/site-packages/onnxruntime/"):
        return "onnxruntime-cuda"
    if lowered.startswith(CUDA_RUNTIME_V2_CUDA_DLL_DIRECTORY.lower() + "/") or lowered.startswith(
        "runtime/" + CUDA_RUNTIME_V2_CUDA_DLL_DIRECTORY.lower() + "/"
    ):
        return "cuda-cudnn"
    if lowered.startswith("worker/"):
        return "worker"
    return "runtime"


def _required_payload_paths() -> tuple[str, ...]:
    return (
        CUDA_RUNTIME_V2_PYTHON_EXECUTABLE,
        CUDA_RUNTIME_V2_PYTHON_DLL,
        *CUDA_RUNTIME_V2_ORT_FILES,
        CUDA_RUNTIME_V2_WORKER_ENTRY,
        CUDA_RUNTIME_V2_WORKER_LAUNCHER,
        CUDA_RUNTIME_V2_WORKER_PYTHON_LAUNCHER,
        *(f"{CUDA_RUNTIME_V2_CUDA_DLL_DIRECTORY}/{name}" for name in CUDA_RUNTIME_V2_REQUIRED_DLLS),
    )


def _ensure_regular_file(path: Path, label: str) -> Path:
    try:
        is_link = path.is_symlink()
        is_file = path.is_file()
    except OSError as exc:
        raise CudaRuntimeV2Error(f"cannot inspect {label}: {path}") from exc
    if is_link or not is_file:
        raise CudaRuntimeV2Error(f"{label} is not a regular file: {path}")
    return path


def collect_payload_files(
    payload_root: Path,
    *,
    cancellation_check: CancellationCheck | None = None,
) -> tuple[RuntimeFile, ...]:
    """Hash every regular payload file and reject links or unsafe paths."""
    root = Path(payload_root).resolve()
    if not root.is_dir():
        raise CudaRuntimeV2Error(f"payload root does not exist: {root}")
    records: list[RuntimeFile] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if cancellation_check is not None:
            cancellation_check()
        if not path.is_file():
            if path.is_symlink():
                raise CudaRuntimeV2Error(f"payload contains a symbolic link: {path}")
            continue
        relative = _relative_path(root, path)
        _ensure_regular_file(path, relative)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise CudaRuntimeV2Error(f"cannot stat payload file: {path}") from exc
        records.append(
            RuntimeFile(
                path=relative,
                role=_classify_role(relative),
                size=size,
                sha256=sha256_file(path, cancellation_check=cancellation_check),
            )
        )
    if not records:
        raise CudaRuntimeV2Error("payload is empty")
    if len(records) > _MAX_MANIFEST_FILES:
        raise CudaRuntimeV2Error("payload has too many files")
    return tuple(sorted(records, key=lambda item: item.path))


def _bundle_id(files: Sequence[RuntimeFile]) -> str:
    identity = "\n".join(
        f"{item.path}\0{item.size}\0{item.sha256}" for item in files
    ).encode("utf-8")
    return f"{CUDA_RUNTIME_V2_RELEASE}-{hashlib.sha256(identity).hexdigest()[:16]}"


def build_manifest(
    payload_root: Path,
    *,
    python_version: str = CUDA_RUNTIME_V2_PYTHON_VERSION,
    ort_version: str = CUDA_RUNTIME_V2_ORT_VERSION,
    minimum_driver_version: str = CUDA_RUNTIME_V2_MIN_DRIVER_VERSION,
    cancellation_check: CancellationCheck | None = None,
) -> dict[str, object]:
    """Build and validate a v2 manifest from an assembled runtime tree."""
    files = collect_payload_files(payload_root, cancellation_check=cancellation_check)
    by_path = {item.path: item for item in files}
    missing = [name for name in _required_payload_paths() if name not in by_path]
    if missing:
        raise CudaRuntimeV2Error("payload is missing required files: " + ", ".join(missing))
    minimum_driver_version = str(minimum_driver_version).strip()
    _driver_tuple(minimum_driver_version)
    manifest: dict[str, object] = {
        "format": CUDA_RUNTIME_V2_FORMAT,
        "format_version": CUDA_RUNTIME_V2_FORMAT_VERSION,
        "release": CUDA_RUNTIME_V2_RELEASE,
        "bundle_id": _bundle_id(files),
        "platform": CUDA_RUNTIME_V2_PLATFORM,
        "architecture": CUDA_RUNTIME_V2_ARCHITECTURE,
        "external_prerequisites": [
            {
                "kind": "nvidia_display_driver",
                "minimum_version": minimum_driver_version,
                "network_required": False,
            }
        ],
        "python": {
            "abi": CUDA_RUNTIME_V2_PYTHON_ABI,
            "version": str(python_version),
            "executable": CUDA_RUNTIME_V2_PYTHON_EXECUTABLE,
            "home": "python",
        },
        "onnxruntime": {
            "package": "onnxruntime-gpu",
            "version": str(ort_version),
            "provider": "CUDAExecutionProvider",
            "root": CUDA_RUNTIME_V2_ORT_ROOT,
            "provider_dll": CUDA_RUNTIME_V2_ORT_PROVIDER,
        },
        "cuda": {
            "major": CUDA_RUNTIME_V2_CUDA_MAJOR,
            "cudnn_major": CUDA_RUNTIME_V2_CUDNN_MAJOR,
            "dll_directory": CUDA_RUNTIME_V2_CUDA_DLL_DIRECTORY,
            "required_dlls": list(CUDA_RUNTIME_V2_REQUIRED_DLLS),
        },
        "worker": {
            "protocol": CUDA_RUNTIME_V2_WORKER_PROTOCOL,
            "entry": CUDA_RUNTIME_V2_WORKER_ENTRY,
            "launcher": CUDA_RUNTIME_V2_WORKER_LAUNCHER,
            "python_launcher": CUDA_RUNTIME_V2_WORKER_PYTHON_LAUNCHER,
        },
        "activation": {
            "marker": "runtime.json",
            "ready_field": "bundle_id",
            "atomic": True,
            "verify_before_activate": True,
        },
        "integrity": {
            "algorithm": "sha256",
            "checksums": CUDA_RUNTIME_V2_CHECKSUM_NAME,
            "payload_root": CUDA_RUNTIME_V2_PAYLOAD_ROOT,
            "file_count": len(files),
            "payload_bytes": sum(item.size for item in files),
        },
        "files": [item.as_dict() for item in files],
    }
    validate_manifest(manifest)
    return manifest


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise CudaRuntimeV2Error(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise CudaRuntimeV2Error(f"{label} must be a JSON object")
    return value


def _validate_integrity_entry(item: object) -> RuntimeFile:
    if not isinstance(item, dict):
        raise CudaRuntimeV2Error("manifest file entry must be an object")
    path = _safe_relative_name(item.get("path"))
    role = str(item.get("role") or "").strip()
    if not role:
        raise CudaRuntimeV2Error(f"manifest role is missing: {path}")
    if role != _classify_role(path):
        raise CudaRuntimeV2Error(f"manifest role does not match path: {path}")
    try:
        size = int(item.get("size"))
    except (TypeError, ValueError) as exc:
        raise CudaRuntimeV2Error(f"manifest size is invalid: {path}") from exc
    digest = str(item.get("sha256") or "").lower()
    if size < 0 or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise CudaRuntimeV2Error(f"manifest integrity entry is invalid: {path}")
    return RuntimeFile(path=path, role=role, size=size, sha256=digest)


def _integer_field(value: object, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise CudaRuntimeV2Error(f"{label} must be an integer")
    if isinstance(value, float) and not value.is_integer():
        raise CudaRuntimeV2Error(f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CudaRuntimeV2Error(f"{label} must be an integer") from exc
    if minimum is not None and result < minimum:
        raise CudaRuntimeV2Error(f"{label} is out of range")
    return result


def validate_manifest(manifest: Mapping[str, object]) -> dict[str, object]:
    """Validate v2 structure and the complete required component boundary."""
    if manifest.get("format") != CUDA_RUNTIME_V2_FORMAT:
        raise CudaRuntimeV2Error(f"unsupported v2 bundle format: {manifest.get('format')!r}")
    if manifest.get("format_version") != CUDA_RUNTIME_V2_FORMAT_VERSION:
        raise CudaRuntimeV2Error("unsupported v2 bundle format version")
    if manifest.get("platform") != CUDA_RUNTIME_V2_PLATFORM or manifest.get("architecture") != CUDA_RUNTIME_V2_ARCHITECTURE:
        raise CudaRuntimeV2Error("v2 bundle platform or architecture is unsupported")
    if not str(manifest.get("release") or "").strip():
        raise CudaRuntimeV2Error("v2 bundle release is missing")
    bundle_id = str(manifest.get("bundle_id") or "").strip()
    if not bundle_id:
        raise CudaRuntimeV2Error("v2 bundle id is missing")

    prerequisites = manifest.get("external_prerequisites")
    if not isinstance(prerequisites, list) or len(prerequisites) != 1:
        raise CudaRuntimeV2Error("v2 must declare exactly one external prerequisite")
    prerequisite = prerequisites[0]
    if not isinstance(prerequisite, dict) or prerequisite.get("kind") != "nvidia_display_driver":
        raise CudaRuntimeV2Error("the only v2 prerequisite must be the NVIDIA display driver")
    if prerequisite.get("network_required") is not False:
        raise CudaRuntimeV2Error("NVIDIA driver prerequisite must be offline")
    minimum_driver = str(prerequisite.get("minimum_version") or "").strip()
    _driver_tuple(minimum_driver)

    python = manifest.get("python")
    if not isinstance(python, dict):
        raise CudaRuntimeV2Error("v2 Python component is missing")
    if python.get("abi") != CUDA_RUNTIME_V2_PYTHON_ABI:
        raise CudaRuntimeV2Error("v2 bundle must carry the pinned CPython ABI")
    if _safe_relative_name(python.get("executable")) != CUDA_RUNTIME_V2_PYTHON_EXECUTABLE:
        raise CudaRuntimeV2Error("v2 Python executable path is invalid")
    if _safe_relative_name(python.get("home")) != "python":
        raise CudaRuntimeV2Error("v2 Python home must be bundled")
    if python.get("version") != CUDA_RUNTIME_V2_PYTHON_VERSION:
        raise CudaRuntimeV2Error("v2 Python version is unsupported")

    ort = manifest.get("onnxruntime")
    if not isinstance(ort, dict):
        raise CudaRuntimeV2Error("v2 ONNX Runtime component is missing")
    if ort.get("package") != "onnxruntime-gpu" or ort.get("provider") != "CUDAExecutionProvider":
        raise CudaRuntimeV2Error("v2 ONNX Runtime provider must be CUDAExecutionProvider")
    if ort.get("version") != CUDA_RUNTIME_V2_ORT_VERSION:
        raise CudaRuntimeV2Error("v2 ONNX Runtime version is unsupported")
    if _safe_relative_name(ort.get("provider_dll")) != CUDA_RUNTIME_V2_ORT_PROVIDER:
        raise CudaRuntimeV2Error("v2 provider DLL path is invalid")
    if _safe_relative_name(ort.get("root")) != CUDA_RUNTIME_V2_ORT_ROOT:
        raise CudaRuntimeV2Error("v2 ORT root path is invalid")

    cuda = manifest.get("cuda")
    if not isinstance(cuda, dict):
        raise CudaRuntimeV2Error("v2 CUDA component is missing")
    if _integer_field(cuda.get("major", -1), "CUDA major") != CUDA_RUNTIME_V2_CUDA_MAJOR or _integer_field(cuda.get("cudnn_major", -1), "cuDNN major") != CUDA_RUNTIME_V2_CUDNN_MAJOR:
        raise CudaRuntimeV2Error("v2 CUDA/cuDNN major versions are unsupported")
    if _safe_relative_name(cuda.get("dll_directory")) != CUDA_RUNTIME_V2_CUDA_DLL_DIRECTORY:
        raise CudaRuntimeV2Error("v2 CUDA DLL directory is invalid")
    required_dlls = tuple(str(item) for item in (cuda.get("required_dlls") or ()))
    if required_dlls != CUDA_RUNTIME_V2_REQUIRED_DLLS or any(
        _safe_relative_name(name) != name or "/" in name for name in required_dlls
    ):
        raise CudaRuntimeV2Error("v2 CUDA DLL list does not match the pinned contract")

    worker = manifest.get("worker")
    if not isinstance(worker, dict) or worker.get("protocol") != CUDA_RUNTIME_V2_WORKER_PROTOCOL:
        raise CudaRuntimeV2Error("v2 Worker protocol is missing or unsupported")
    if _safe_relative_name(worker.get("entry")) != CUDA_RUNTIME_V2_WORKER_ENTRY:
        raise CudaRuntimeV2Error("v2 Worker entry path is invalid")
    if _safe_relative_name(worker.get("launcher")) != CUDA_RUNTIME_V2_WORKER_LAUNCHER:
        raise CudaRuntimeV2Error("v2 Worker launcher path is invalid")
    if _safe_relative_name(worker.get("python_launcher")) != CUDA_RUNTIME_V2_WORKER_PYTHON_LAUNCHER:
        raise CudaRuntimeV2Error("v2 Python launcher path is invalid")

    activation = manifest.get("activation")
    if not isinstance(activation, dict) or activation.get("atomic") is not True or activation.get("verify_before_activate") is not True:
        raise CudaRuntimeV2Error("v2 activation must be atomic and verification-gated")

    integrity = manifest.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("algorithm") != "sha256":
        raise CudaRuntimeV2Error("v2 manifest must use SHA-256 integrity metadata")
    if integrity.get("checksums") != CUDA_RUNTIME_V2_CHECKSUM_NAME or integrity.get("payload_root") != CUDA_RUNTIME_V2_PAYLOAD_ROOT:
        raise CudaRuntimeV2Error("v2 integrity metadata points to an invalid checksum root")

    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise CudaRuntimeV2Error("v2 manifest has no payload files")
    if len(raw_files) > _MAX_MANIFEST_FILES:
        raise CudaRuntimeV2Error("v2 manifest has too many files")
    files: list[RuntimeFile] = []
    seen: set[str] = set()
    for raw in raw_files:
        item = _validate_integrity_entry(raw)
        if item.path in seen:
            raise CudaRuntimeV2Error(f"duplicate v2 manifest file: {item.path}")
        seen.add(item.path)
        files.append(item)
    required = set(_required_payload_paths())
    missing = sorted(required - seen)
    if missing:
        raise CudaRuntimeV2Error("v2 manifest is missing required files: " + ", ".join(missing))
    expected_file_count = _integer_field(integrity.get("file_count"), "manifest file_count", minimum=1)
    expected_payload_bytes = _integer_field(integrity.get("payload_bytes"), "manifest payload_bytes", minimum=0)
    if expected_file_count != len(files) or expected_payload_bytes != sum(item.size for item in files):
        raise CudaRuntimeV2Error("v2 manifest aggregate integrity metadata is inconsistent")
    if _bundle_id(tuple(sorted(files, key=lambda item: item.path))) != bundle_id:
        raise CudaRuntimeV2Error("v2 bundle_id does not match the file integrity metadata")
    return dict(manifest)


def _read_checksums(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CudaRuntimeV2Error(f"cannot read checksum list: {path}") from exc
    result: dict[str, str] = {}
    for line in lines:
        value = line.strip()
        if not value:
            continue
        parts = value.split(None, 1)
        if len(parts) != 2:
            raise CudaRuntimeV2Error("invalid line in SHA256SUMS.txt")
        digest, raw_path = parts
        relative = _safe_relative_name(raw_path)
        digest = digest.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise CudaRuntimeV2Error(f"invalid checksum for {relative}")
        if relative in result:
            raise CudaRuntimeV2Error(f"duplicate checksum entry: {relative}")
        result[relative] = digest
    return result


def validate_payload_tree(
    bundle_root: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> dict[str, object]:
    """Validate an extracted v2 archive and every payload digest."""
    # Keep the input path spelling stable for isolated child environments.
    root = Path(bundle_root)
    _ensure_regular_file(root / CUDA_RUNTIME_V2_MANIFEST_NAME, CUDA_RUNTIME_V2_MANIFEST_NAME)
    _ensure_regular_file(root / CUDA_RUNTIME_V2_CHECKSUM_NAME, CUDA_RUNTIME_V2_CHECKSUM_NAME)
    manifest = validate_manifest(_read_json(root / CUDA_RUNTIME_V2_MANIFEST_NAME, "manifest"))
    payload_root = root / CUDA_RUNTIME_V2_PAYLOAD_ROOT
    if not payload_root.is_dir() or payload_root.is_symlink():
        raise CudaRuntimeV2Error("v2 payload directory is missing or is a link")
    allowed_top_level = {
        CUDA_RUNTIME_V2_MANIFEST_NAME,
        CUDA_RUNTIME_V2_CHECKSUM_NAME,
        CUDA_RUNTIME_V2_PAYLOAD_ROOT,
    }
    unexpected_top_level = sorted(
        path.name for path in root.iterdir() if path.name not in allowed_top_level
    )
    if unexpected_top_level:
        raise CudaRuntimeV2Error(
            "unexpected top-level v2 bundle members: " + ", ".join(unexpected_top_level)
        )
    checksums = _read_checksums(root / CUDA_RUNTIME_V2_CHECKSUM_NAME)
    expected = {_safe_relative_name(item["path"]): item for item in manifest["files"]}
    actual: set[str] = set()
    for path in payload_root.rglob("*"):
        if cancellation_check is not None:
            cancellation_check()
        if path.is_symlink():
            raise CudaRuntimeV2Error(f"v2 payload contains a symbolic link: {path}")
        if path.is_file():
            actual.add(_relative_path(payload_root, path))
    expected_names = set(expected)
    if actual != expected_names:
        missing = sorted(expected_names - actual)
        unexpected = sorted(actual - expected_names)
        raise CudaRuntimeV2Error(
            f"v2 payload file set mismatch; missing={missing}, unexpected={unexpected}"
        )
    if set(checksums) != expected_names:
        missing = sorted(expected_names - set(checksums))
        unexpected = sorted(set(checksums) - expected_names)
        raise CudaRuntimeV2Error(
            f"v2 checksum file set mismatch; missing={missing}, unexpected={unexpected}"
        )

    total = sum(int(item["size"]) for item in manifest["files"])
    verified = 0
    for item in sorted(manifest["files"], key=lambda value: str(value["path"])):
        if cancellation_check is not None:
            cancellation_check()
        relative = _safe_relative_name(item["path"])
        path = payload_root / Path(*relative.split("/"))
        _ensure_regular_file(path, relative)
        expected_size = _integer_field(item["size"], f"payload size for {relative}", minimum=0)
        if path.stat().st_size != expected_size:
            raise CudaRuntimeV2Error(f"v2 payload size mismatch: {relative}")
        base = verified
        digest = sha256_file(
            path,
            progress_callback=(
                None
                if progress_callback is None
                else lambda current, _file_total, base=base: progress_callback(base + current, total)
            ),
            cancellation_check=cancellation_check,
        )
        verified += expected_size
        if digest != str(item["sha256"]).lower() or checksums.get(relative) != digest:
            raise CudaRuntimeV2Error(f"v2 payload hash mismatch: {relative}")
    return manifest


def _validate_archive_member(info: zipfile.ZipInfo) -> str:
    name = _safe_relative_name(info.filename)
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
        raise CudaRuntimeV2Error(f"archive member is not a regular file: {name}")
    return name


def safe_extract_bundle(
    archive_path: Path,
    destination: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> None:
    """Safely extract a v2 ZIP, rejecting links, duplicates and traversal."""
    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise CudaRuntimeV2Error("v2 runtime archive is not a valid ZIP") from exc
    with archive:
        members: list[tuple[zipfile.ZipInfo, str]] = []
        seen: set[str] = set()
        total = 0
        for info in archive.infolist():
            if cancellation_check is not None:
                cancellation_check()
            name = _validate_archive_member(info)
            if name in seen:
                raise CudaRuntimeV2Error(f"duplicate archive member: {name}")
            seen.add(name)
            total += max(0, int(info.file_size))
            if total > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise CudaRuntimeV2Error("v2 archive is unreasonably large")
            members.append((info, name))
        extracted = 0
        for info, name in members:
            if cancellation_check is not None:
                cancellation_check()
            output = (target / Path(*name.split("/"))).resolve()
            try:
                output.relative_to(target)
            except ValueError as exc:
                raise CudaRuntimeV2Error(f"archive member escapes extraction root: {name}") from exc
            if info.is_dir() or info.filename.endswith(("/", "\\")):
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, output.open("wb") as sink:
                while True:
                    if cancellation_check is not None:
                        cancellation_check()
                    chunk = source.read(_HASH_CHUNK_SIZE)
                    if not chunk:
                        break
                    sink.write(chunk)
                    extracted += len(chunk)
                    if progress_callback is not None:
                        progress_callback(extracted, total)


def _is_ambient_runtime_key(key: object) -> bool:
    normalized = str(key or "").upper()
    return (
        normalized == "PATH"
        or normalized.startswith("PYTHON")
        or normalized.startswith("PYENV")
        or normalized.startswith("CONDA")
        or normalized.startswith("VIRTUAL_ENV")
        or normalized in {
            "__PYVENV_LAUNCHER__",
            "_CE_CONDA",
            "_CE_M",
            "CUDA_HOME",
            "CUDNN_HOME",
            "CUDNN_PATH",
            "NVTOOLSEXT_PATH",
            "LD_LIBRARY_PATH",
            "DYLD_LIBRARY_PATH",
            "PIPENV_ACTIVE",
            "PIPENV_PIPFILE",
            "POETRY_ACTIVE",
        }
        or normalized.startswith("CUDA_PATH")
    )


def _launcher_environment(bundle_root: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    root = Path(bundle_root)
    python_home = root / "python"
    site_packages = root / "runtime" / "Lib" / "site-packages"
    worker = root / "worker"
    cuda_bin = root / Path(*CUDA_RUNTIME_V2_CUDA_DLL_DIRECTORY.split("/"))
    ambient = base if base is not None else os.environ
    environment = {
        str(key): str(value)
        for key, value in ambient.items()
        if not _is_ambient_runtime_key(key)
    }
    environment["PYTHONHOME"] = str(python_home)
    environment["PYTHONPATH"] = os.pathsep.join((str(site_packages), str(worker)))
    # Keep DLL lookup self-contained. Windows still resolves protected system
    # DLLs normally; user-installed CUDA/Python paths cannot influence Worker.
    environment["PATH"] = os.pathsep.join(
        (str(python_home), str(python_home / "Scripts"), str(cuda_bin))
    )
    # Prevent a user site or an ambient virtualenv from changing imports.
    environment["PYTHONNOUSERSITE"] = "1"
    environment["FSV_CUDA_RUNTIME_ROOT"] = str(root)
    return environment


def worker_launch_command(
    bundle_root: Path,
    arguments: Iterable[str] = (),
    *,
    base_environment: Mapping[str, str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Return a process command that uses only files inside ``bundle_root``."""
    root = Path(bundle_root)
    python = root / Path(*CUDA_RUNTIME_V2_PYTHON_EXECUTABLE.split("/"))
    entry = root / Path(*CUDA_RUNTIME_V2_WORKER_ENTRY.split("/"))
    launcher = root / Path(*CUDA_RUNTIME_V2_WORKER_PYTHON_LAUNCHER.split("/"))
    try:
        _ensure_regular_file(python, "v2 bundled Python")
        _ensure_regular_file(entry, "v2 Worker entry")
        _ensure_regular_file(launcher, "v2 Python launcher")
    except CudaRuntimeV2Error as exc:
        raise CudaRuntimeV2Error(
            "v2 bundled Python, launcher or Worker entry is missing or unsafe"
        ) from exc
    # Isolated mode ignores ambient PYTHON* settings during interpreter startup.
    # The hashed launcher adds only the bundle's own runtime paths before it
    # imports the Worker.
    command = [str(python), "-I", str(launcher), *[str(item) for item in arguments]]
    return command, _launcher_environment(root, base_environment)


def write_worker_launchers(payload_root: Path) -> tuple[Path, Path]:
    """Create deterministic launchers which never resolve system Python."""
    root = Path(payload_root)
    worker_dir = root / "worker"
    entry = root / Path(*CUDA_RUNTIME_V2_WORKER_ENTRY.split("/"))
    if not entry.is_file():
        raise CudaRuntimeV2Error(f"Worker entry is missing: {entry}")
    worker_dir.mkdir(parents=True, exist_ok=True)
    cmd_path = root / Path(*CUDA_RUNTIME_V2_WORKER_LAUNCHER.split("/"))
    py_path = root / Path(*CUDA_RUNTIME_V2_WORKER_PYTHON_LAUNCHER.split("/"))
    for launcher_path in (cmd_path, py_path):
        if launcher_path.is_symlink():
            raise CudaRuntimeV2Error(f"launcher path is a symbolic link: {launcher_path}")
    cmd_text = """@echo off\r\nsetlocal\r\nset \"FSV_CUDA_ROOT=%~dp0..\"\r\nset \"PYTHONHOME=%FSV_CUDA_ROOT%\\python\"\r\nset \"PYTHONPATH=%FSV_CUDA_ROOT%\\runtime\\Lib\\site-packages;%FSV_CUDA_ROOT%\\worker\"\r\nset \"PATH=%FSV_CUDA_ROOT%\\python;%FSV_CUDA_ROOT%\\python\\Scripts;%FSV_CUDA_ROOT%\\runtime\\cuda\\bin\"\r\nset \"PYTHONNOUSERSITE=1\"\r\n\"%FSV_CUDA_ROOT%\\python\\python.exe\" -I \"%FSV_CUDA_ROOT%\\worker\\launch_cuda_worker.py\" %*\r\nexit /b %ERRORLEVEL%\r\n"""
    py_text = '''"""v2 Worker launcher; execute with the bundled CPython."""\nfrom __future__ import annotations\n\nimport os\nfrom pathlib import Path\nimport runpy\nimport sys\n\n_ROOT = Path(__file__).resolve().parents[1]\n_PYTHON_HOME = _ROOT / "python"\n_SITE_PACKAGES = _ROOT / "runtime" / "Lib" / "site-packages"\n_WORKER = _ROOT / "worker"\n_CUDA_BIN = _ROOT / "runtime" / "cuda" / "bin"\nos.environ["PYTHONHOME"] = str(_PYTHON_HOME)\nos.environ["PYTHONPATH"] = os.pathsep.join((str(_SITE_PACKAGES), str(_WORKER)))\nos.environ["PYTHONNOUSERSITE"] = "1"\nos.environ["PATH"] = os.pathsep.join((str(_PYTHON_HOME), str(_PYTHON_HOME / "Scripts"), str(_CUDA_BIN)))\nsys.path[:0] = [str(_SITE_PACKAGES), str(_WORKER)]\nrunpy.run_path(str(_WORKER / "cuda_worker.py"), run_name="__main__")\n'''
    # Scrub activation variables before the launcher imports any third-party
    # module.  ``-I`` handles interpreter startup; this covers child-process
    # launchers and libraries that inspect their parent environment directly.
    py_scrub = '''for _key in tuple(os.environ):
    _upper = _key.upper()
    if (_upper == "PATH" or _upper.startswith(("PYTHON", "PYENV", "CONDA", "VIRTUAL_ENV"))
            or _upper.startswith("CUDA_PATH")
            or _upper in {"CUDA_HOME", "CUDNN_HOME", "CUDNN_PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"}):
        os.environ.pop(_key, None)
'''
    py_text = py_text.replace("_ROOT = Path(__file__).resolve().parents[1]", py_scrub + "\n_ROOT = Path(__file__).resolve().parents[1]")
    cmd_path.write_text(cmd_text, encoding="ascii", newline="")
    py_path.write_text(py_text, encoding="utf-8", newline="\n")
    return cmd_path, py_path


def build_bundle(
    source_root: Path,
    output_path: Path,
    *,
    python_version: str = CUDA_RUNTIME_V2_PYTHON_VERSION,
    ort_version: str = CUDA_RUNTIME_V2_ORT_VERSION,
    minimum_driver_version: str = CUDA_RUNTIME_V2_MIN_DRIVER_VERSION,
    generate_launchers: bool = True,
) -> dict[str, object]:
    """Build a deterministic v2 archive from an assembled self-contained tree."""
    source = Path(source_root).resolve()
    if generate_launchers:
        write_worker_launchers(source)
    manifest = build_manifest(
        source,
        python_version=python_version,
        ort_version=ort_version,
        minimum_driver_version=minimum_driver_version,
    )
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    checksums = "".join(
        f"{item['sha256']}  {item['path']}\n"
        for item in manifest["files"]
    ).encode("ascii")

    def info(name: str) -> zipfile.ZipInfo:
        entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry._compresslevel = 9
        entry.create_system = 0
        entry.external_attr = 0o644 << 16
        return entry

    manifest_bytes = _canonical_json(manifest)
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr(info(CUDA_RUNTIME_V2_MANIFEST_NAME), manifest_bytes)
        archive.writestr(info(CUDA_RUNTIME_V2_CHECKSUM_NAME), checksums)
        for item in manifest["files"]:
            relative = str(item["path"])
            source_file = source / Path(*relative.split("/"))
            with source_file.open("rb") as stream, archive.open(info(f"{CUDA_RUNTIME_V2_PAYLOAD_ROOT}/{relative}"), "w") as target:
                shutil.copyfileobj(stream, target, length=_HASH_CHUNK_SIZE)
    temporary.replace(output)
    archive_hash = sha256_file(output)
    release = {
        "archive_name": output.name,
        "archive_bytes": output.stat().st_size,
        "archive_sha256": archive_hash,
        "bundle_id": manifest["bundle_id"],
        "manifest": manifest,
    }
    sidecar = output.with_name(output.name + CUDA_RUNTIME_V2_RELEASE_SUFFIX)
    sidecar.write_bytes(_canonical_json(release))
    return {"archive": str(output), **release}


def verify_driver_requirement(
    manifest: Mapping[str, object],
    *,
    driver_version: str | None = None,
    driver_probe: DriverProbe | None = None,
) -> str | None:
    """Validate the declared driver-only prerequisite and optionally probe it."""
    checked = validate_manifest(manifest)
    prerequisites = checked["external_prerequisites"]
    assert isinstance(prerequisites, list) and prerequisites
    prerequisite = prerequisites[0]
    assert isinstance(prerequisite, dict)
    minimum = str(prerequisite["minimum_version"])
    detected = driver_version
    if detected is None and driver_probe is not None:
        detected = driver_probe()
    if detected is not None and not driver_version_satisfies(str(detected), minimum):
        raise CudaRuntimeV2Error(
            f"NVIDIA display driver {detected} is below required {minimum}"
        )
    return None if detected is None else str(detected)


def verify_archive_offline(
    archive_path: Path,
    *,
    expected_archive_sha256: str | None = None,
    driver_version: str | None = None,
    driver_probe: DriverProbe | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> OfflineVerification:
    """Verify a v2 archive without network access or third-party imports."""
    archive = Path(archive_path).resolve()
    if not archive.is_file():
        raise CudaRuntimeV2Error(f"v2 archive does not exist: {archive}")
    actual_hash = sha256_file(archive, cancellation_check=cancellation_check)
    if expected_archive_sha256 is not None:
        expected = str(expected_archive_sha256).strip().lower()
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise CudaRuntimeV2Error("expected archive SHA-256 is invalid")
        if actual_hash != expected:
            raise CudaRuntimeV2Error(
                f"archive SHA-256 mismatch: {actual_hash} != {expected}"
            )
    with tempfile.TemporaryDirectory(prefix="fsv-cuda-v2-verify-") as temporary:
        extracted = Path(temporary)
        safe_extract_bundle(archive, extracted, cancellation_check=cancellation_check)
        manifest = validate_payload_tree(extracted, cancellation_check=cancellation_check)
        detected = verify_driver_requirement(
            manifest,
            driver_version=driver_version,
            driver_probe=driver_probe,
        )
        payload_root = extracted / CUDA_RUNTIME_V2_PAYLOAD_ROOT
        command, _environment = worker_launch_command(payload_root)
        integrity = manifest["integrity"]
        assert isinstance(integrity, dict)
        prerequisites = manifest["external_prerequisites"]
        assert isinstance(prerequisites, list) and prerequisites
        prerequisite = prerequisites[0]
        assert isinstance(prerequisite, dict)
        return OfflineVerification(
            archive=archive,
            archive_bytes=archive.stat().st_size,
            archive_sha256=actual_hash,
            bundle_id=str(manifest["bundle_id"]),
            file_count=_integer_field(integrity["file_count"], "manifest file_count", minimum=1),
            payload_bytes=_integer_field(integrity["payload_bytes"], "manifest payload_bytes", minimum=0),
            driver_requirement=str(prerequisite["minimum_version"]),
            detected_driver=detected,
            worker_command=tuple(command),
        )


__all__ = [
    "CUDA_RUNTIME_V2_ARCHITECTURE",
    "CUDA_RUNTIME_V2_CUDA_DLL_DIRECTORY",
    "CUDA_RUNTIME_V2_FORMAT_VERSION",
    "CUDA_RUNTIME_V2_FORMAT",
    "CUDA_RUNTIME_V2_MANIFEST_NAME",
    "CUDA_RUNTIME_V2_MIN_DRIVER_VERSION",
    "CUDA_RUNTIME_V2_ORT_CORE",
    "CUDA_RUNTIME_V2_ORT_FILES",
    "CUDA_RUNTIME_V2_ORT_PROVIDER",
    "CUDA_RUNTIME_V2_PAYLOAD_ROOT",
    "CUDA_RUNTIME_V2_PYTHON_ABI",
    "CUDA_RUNTIME_V2_PYTHON_DLL",
    "CUDA_RUNTIME_V2_PYTHON_EXECUTABLE",
    "CUDA_RUNTIME_V2_REQUIRED_DLLS",
    "CUDA_RUNTIME_V2_WORKER_ENTRY",
    "CUDA_RUNTIME_V2_WORKER_LAUNCHER",
    "CUDA_RUNTIME_V2_WORKER_PYTHON_LAUNCHER",
    "CudaRuntimeV2Error",
    "OfflineVerification",
    "RuntimeFile",
    "build_bundle",
    "build_manifest",
    "collect_payload_files",
    "driver_version_satisfies",
    "safe_extract_bundle",
    "sha256_file",
    "validate_manifest",
    "validate_payload_tree",
    "verify_archive_offline",
    "verify_driver_requirement",
    "worker_launch_command",
    "write_worker_launchers",
]
