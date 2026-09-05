"""Validation and extraction helpers for the optional CUDA voice bundle.

This module intentionally uses only the Python standard library.  It is used
by both the offline bundle builder and the online installer before any native
library is loaded.
"""

from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from . import voice_runtime_contract as contract


class CudaBundleError(RuntimeError):
    """Raised when a CUDA bundle is malformed or fails integrity checks."""


_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
_HASH_CHUNK_SIZE = 1024 * 1024
ProgressCallback = Callable[[int, int], None]
CancellationCheck = Callable[[], None]


def _safe_relative_name(value: object) -> str:
    """Normalize a bundle path and reject absolute or parent-relative names."""
    text = str(value or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or ":" in text.split("/", 1)[0]:
        raise CudaBundleError(f"bundle path is not relative: {value!r}")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CudaBundleError(f"bundle path is unsafe: {value!r}")
    return path.as_posix()


def sha256_file(
    path: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> str:
    digest = hashlib.sha256()
    total = max(0, Path(path).stat().st_size)
    current = 0
    with Path(path).open("rb") as stream:
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


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise CudaBundleError(f"cannot read bundle manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise CudaBundleError("bundle manifest must be an object")
    return payload


def validate_bundle_manifest(manifest: dict) -> dict:
    """Validate the structural fields shared by builder and installer."""
    if manifest.get("format") != contract.CUDA_RUNTIME_BUNDLE_FORMAT:
        raise CudaBundleError(f"unsupported bundle format: {manifest.get('format')!r}")
    if manifest.get("format_version") != contract.CUDA_RUNTIME_BUNDLE_FORMAT_VERSION:
        raise CudaBundleError(
            f"unsupported bundle format version: {manifest.get('format_version')!r}"
        )
    if manifest.get("python_abi") != contract.CUDA_RUNTIME_ABI:
        raise CudaBundleError(f"unsupported Python ABI: {manifest.get('python_abi')!r}")
    if manifest.get("onnxruntime_version") != contract.CUDA_RUNTIME_VERSION:
        raise CudaBundleError(
            f"unsupported ONNX Runtime version: {manifest.get('onnxruntime_version')!r}"
        )
    if manifest.get("provider") != "CUDAExecutionProvider":
        raise CudaBundleError(f"unsupported provider: {manifest.get('provider')!r}")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise CudaBundleError("bundle manifest has no payload files")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise CudaBundleError("bundle file entry must be an object")
        name = _safe_relative_name(item.get("path"))
        if name in seen:
            raise CudaBundleError(f"duplicate bundle file: {name}")
        seen.add(name)
        try:
            size = int(item.get("size"))
        except (TypeError, ValueError) as exc:
            raise CudaBundleError(f"invalid size for bundle file: {name}") from exc
        digest = str(item.get("sha256") or "").lower()
        if size < 0 or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise CudaBundleError(f"invalid integrity entry for bundle file: {name}")

    required = tuple(manifest.get("required_dlls") or ())
    if required != contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS:
        raise CudaBundleError("bundle required DLL list does not match the runtime contract")
    dll_directory = _safe_relative_name(manifest.get("dll_directory"))
    if dll_directory != contract.CUDA_RUNTIME_BUNDLE_DLL_DIRECTORY:
        raise CudaBundleError("bundle DLL directory does not match the runtime contract")
    if manifest.get("payload_root") != contract.CUDA_RUNTIME_BUNDLE_PAYLOAD_ROOT:
        raise CudaBundleError("bundle payload root does not match the runtime contract")
    file_names = seen
    for dll_name in required:
        expected = f"{dll_directory}/{dll_name}"
        if expected not in file_names:
            raise CudaBundleError(f"required CUDA DLL is missing from manifest: {dll_name}")
    provider_path = "Lib/site-packages/onnxruntime/capi/onnxruntime_providers_cuda.dll"
    if provider_path not in file_names:
        raise CudaBundleError("CUDA execution provider DLL is missing from bundle")
    return manifest


def validate_bundle_tree(
    bundle_root: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> dict:
    """Validate an extracted bundle and all payload file digests."""
    root = Path(bundle_root).resolve()
    manifest = validate_bundle_manifest(_read_json(root / "bundle.json"))
    payload_root = root / contract.CUDA_RUNTIME_BUNDLE_PAYLOAD_ROOT
    if not payload_root.is_dir():
        raise CudaBundleError("bundle payload directory is missing")

    sums: dict[str, str] = {}
    sums_path = root / "SHA256SUMS.txt"
    try:
        lines = sums_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CudaBundleError("bundle SHA256SUMS.txt is missing or unreadable") from exc
    for line in lines:
        value = line.strip()
        if not value:
            continue
        parts = value.split(None, 1)
        if len(parts) != 2:
            raise CudaBundleError("invalid line in bundle SHA256SUMS.txt")
        digest, name = parts
        normalized = _safe_relative_name(name)
        if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            raise CudaBundleError(f"invalid hash in bundle SHA256SUMS.txt: {name}")
        if normalized in sums:
            raise CudaBundleError(f"duplicate hash entry: {normalized}")
        sums[normalized] = digest.lower()

    expected_payload_files = {
        _safe_relative_name(item["path"])
        for item in manifest["files"]
    }
    actual_payload_files = {
        path.relative_to(payload_root).as_posix()
        for path in payload_root.rglob("*")
        if path.is_file()
    }
    if actual_payload_files != expected_payload_files:
        missing = sorted(expected_payload_files - actual_payload_files)
        unexpected = sorted(actual_payload_files - expected_payload_files)
        raise CudaBundleError(
            f"bundle payload file set mismatch; missing={missing}, unexpected={unexpected}"
        )
    allowed_metadata = {
        "bundle.json",
        "SHA256SUMS.txt",
        "THIRD_PARTY_NOTICES.txt",
    }
    for path in root.iterdir():
        if path == payload_root or path.name in allowed_metadata:
            continue
        raise CudaBundleError(f"unexpected top-level bundle member: {path.name}")

    total_payload_bytes = sum(max(0, int(item["size"])) for item in manifest["files"])
    verified_bytes = 0
    for item in manifest["files"]:
        if cancellation_check is not None:
            cancellation_check()
        relative = _safe_relative_name(item["path"])
        path = payload_root / Path(*relative.split("/"))
        if not path.is_file():
            raise CudaBundleError(f"bundle payload file is missing: {relative}")
        expected_size = int(item["size"])
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise CudaBundleError(
                f"bundle payload size mismatch for {relative}: {actual_size}/{expected_size}"
            )
        file_base = verified_bytes
        actual_hash = sha256_file(
            path,
            progress_callback=(
                None
                if progress_callback is None
                else lambda current, _total, base=file_base: progress_callback(
                    base + current,
                    total_payload_bytes,
                )
            ),
            cancellation_check=cancellation_check,
        )
        verified_bytes += actual_size
        if actual_hash != str(item["sha256"]).lower():
            raise CudaBundleError(f"bundle payload hash mismatch: {relative}")
        if sums.get(relative) != actual_hash:
            raise CudaBundleError(f"bundle checksum list mismatch: {relative}")

    return manifest


def _validate_zip_member(info: zipfile.ZipInfo) -> str:
    name = _safe_relative_name(info.filename)
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
        raise CudaBundleError(f"archive member is not a regular file: {name}")
    return name


def safe_extract_zip(
    archive_path: Path,
    destination: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> None:
    """Extract a bundle ZIP after validating every member and its size."""
    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    total_size = 0
    seen: set[str] = set()
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise CudaBundleError("CUDA runtime bundle is not a valid ZIP") from exc
    with archive:
        members: list[tuple[zipfile.ZipInfo, str]] = []
        for info in archive.infolist():
            if cancellation_check is not None:
                cancellation_check()
            name = _validate_zip_member(info)
            if name in seen:
                raise CudaBundleError(f"duplicate archive member: {name}")
            seen.add(name)
            total_size += max(0, int(info.file_size))
            if total_size > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise CudaBundleError("CUDA runtime bundle is unreasonably large")
            members.append((info, name))

        extracted_bytes = 0
        for info, name in members:
            if cancellation_check is not None:
                cancellation_check()
            output = (target / Path(*name.split("/"))).resolve()
            try:
                output.relative_to(target)
            except ValueError as exc:
                raise CudaBundleError(f"archive member escapes extraction root: {name}") from exc
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
                    extracted_bytes += len(chunk)
                    if progress_callback is not None:
                        progress_callback(extracted_bytes, total_size)


__all__ = [
    "CudaBundleError",
    "safe_extract_zip",
    "sha256_file",
    "validate_bundle_manifest",
    "validate_bundle_tree",
]
