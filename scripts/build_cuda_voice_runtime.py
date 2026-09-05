#!/usr/bin/env python3
"""Build the trimmed, reproducible CUDA runtime bundle for ONNX voice.

The source is an ordinary pip-installed ORT CUDA environment.  The output is
not a wheel and does not contain a Python interpreter; installation creates a
small venv and places only the files described by ``bundle.json`` inside it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.core import voice_runtime_contract as contract
from lib.core.cuda_runtime_bundle import (
    CudaBundleError,
    sha256_file,
    validate_bundle_manifest,
)


class BundleBuildError(RuntimeError):
    """Raised when the source runtime cannot produce a valid bundle."""


@dataclass(frozen=True)
class PayloadFile:
    source: Path
    relative: str
    size: int
    sha256: str


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _ensure_regular_file(path: Path, label: str) -> Path:
    if not path.is_file() or path.is_symlink():
        raise BundleBuildError(f"{label} is missing or is not a regular file: {path}")
    return path


def _find_nvidia_dll(site_packages: Path, name: str) -> Path:
    matches = sorted(
        path
        for path in site_packages.glob(f"nvidia/*/bin/{name}")
        if path.is_file() and not path.is_symlink()
    )
    if len(matches) != 1:
        raise BundleBuildError(
            f"expected exactly one source for {name}, found {len(matches)}"
        )
    return matches[0]


def _read_ort_version(ort_root: Path) -> str:
    text = (ort_root / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _read_voice_manifest(package_root: Path | None) -> dict | None:
    if package_root is None:
        return None
    path = Path(package_root) / "manifest.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _installed_distribution_version(site_packages: Path, normalized_name: str) -> str:
    prefix = normalized_name.replace("-", "_").lower() + "-"
    matches = sorted(
        path
        for path in site_packages.glob("*.dist-info")
        if path.name.lower().startswith(prefix)
    )
    if len(matches) != 1:
        return ""
    metadata = matches[0] / "METADATA"
    try:
        lines = metadata.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
    return ""


def collect_payload(source_root: Path) -> list[PayloadFile]:
    """Collect the ORT Python surface and the observed CUDA DLL set."""
    root = Path(source_root).resolve()
    site_packages = root / "Lib" / "site-packages"
    ort_root = site_packages / "onnxruntime"
    if not ort_root.is_dir():
        raise BundleBuildError(f"onnxruntime package is missing: {ort_root}")
    version = _read_ort_version(ort_root)
    if version != contract.CUDA_RUNTIME_VERSION:
        raise BundleBuildError(
            f"source ONNX Runtime version {version!r} does not match "
            f"{contract.CUDA_RUNTIME_VERSION!r}"
        )

    sources: list[tuple[Path, str]] = []
    for relative in contract.CUDA_RUNTIME_BUNDLE_ORT_FILES:
        sources.append((ort_root / Path(*relative.split("/")), f"Lib/site-packages/onnxruntime/{relative}"))
    for name in contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS:
        sources.append(
            (
                _find_nvidia_dll(site_packages, name),
                f"{contract.CUDA_RUNTIME_BUNDLE_DLL_DIRECTORY}/{name}",
            )
        )

    seen: set[str] = set()
    payload: list[PayloadFile] = []
    for source, relative in sources:
        if relative in seen:
            raise BundleBuildError(f"duplicate payload destination: {relative}")
        seen.add(relative)
        source = _ensure_regular_file(source, relative)
        size, digest = _hash_file(source)
        payload.append(PayloadFile(source, relative, size, digest))
    return payload


def _notices(source_root: Path, payload: list[PayloadFile]) -> bytes:
    """Collect redistributable notices without shipping pip metadata."""
    site_packages = Path(source_root) / "Lib" / "site-packages"
    candidates = [
        site_packages / "onnxruntime" / "LICENSE",
        site_packages / "onnxruntime" / "ThirdPartyNotices.txt",
    ]
    included_dll_names = {
        Path(item.relative).name for item in payload
        if item.relative.startswith(contract.CUDA_RUNTIME_BUNDLE_DLL_DIRECTORY + "/")
    }
    if included_dll_names:
        for dist_info in sorted(site_packages.glob("nvidia_*.dist-info")):
            for license_path in sorted(dist_info.glob("licenses/*")):
                if license_path.is_file():
                    candidates.append(license_path)

    sections: list[str] = []
    seen_hashes: set[str] = set()
    for path in candidates:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        try:
            label = path.relative_to(site_packages).as_posix()
        except ValueError:
            label = path.name
        sections.append(f"===== {label} =====\n{text.rstrip()}\n")
    if not sections:
        sections.append("No third-party notices were found in the source environment.\n")
    return "\n".join(sections).encode("utf-8")


def _canonical_json(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _zip_info(name: str, compression: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compression
    info._compresslevel = 9
    info.create_system = 0
    info.external_attr = 0o644 << 16
    return info


def _write_member_from_file(archive: zipfile.ZipFile, name: str, source: Path, compression: int) -> None:
    info = _zip_info(name, compression)
    with source.open("rb") as stream, archive.open(info, "w") as target:
        shutil.copyfileobj(stream, target, length=1024 * 1024)


def _write_member_bytes(archive: zipfile.ZipFile, name: str, payload: bytes, compression: int) -> None:
    archive.writestr(_zip_info(name, compression), payload)


def build_bundle(
    source_root: Path,
    output_path: Path,
    *,
    voice_package_root: Path | None = None,
) -> dict:
    payload = collect_payload(source_root)
    identity = "\n".join(f"{item.relative}\0{item.sha256}" for item in payload).encode("utf-8")
    bundle_id = f"{contract.CUDA_RUNTIME_BUNDLE_RELEASE}-{hashlib.sha256(identity).hexdigest()[:16]}"
    voice_manifest = _read_voice_manifest(voice_package_root)
    site_packages = Path(source_root) / "Lib" / "site-packages"
    tested_voice = None
    if voice_manifest is not None:
        tested_voice = {
            key: voice_manifest.get(key)
            for key in ("format", "format_version", "runtime_revision", "name", "precision_profile")
            if key in voice_manifest
        }
    manifest = {
        "format": contract.CUDA_RUNTIME_BUNDLE_FORMAT,
        "format_version": contract.CUDA_RUNTIME_BUNDLE_FORMAT_VERSION,
        "release": contract.CUDA_RUNTIME_BUNDLE_RELEASE,
        "bundle_id": bundle_id,
        "provider": "CUDAExecutionProvider",
        "onnxruntime_version": contract.CUDA_RUNTIME_VERSION,
        "cuda_major": 12,
        "cudnn_major": 9,
        "component_versions": {
            name: _installed_distribution_version(site_packages, name)
            for name in (
                "onnxruntime-gpu",
                "nvidia-cuda-runtime-cu12",
                "nvidia-cublas-cu12",
                "nvidia-cufft-cu12",
                "nvidia-cudnn-cu12",
            )
        },
        "python_abi": contract.CUDA_RUNTIME_ABI,
        "payload_root": contract.CUDA_RUNTIME_BUNDLE_PAYLOAD_ROOT,
        "dll_directory": contract.CUDA_RUNTIME_BUNDLE_DLL_DIRECTORY,
        "required_dlls": list(contract.CUDA_RUNTIME_BUNDLE_REQUIRED_DLLS),
        "files": [
            {"path": item.relative, "size": item.size, "sha256": item.sha256}
            for item in sorted(payload, key=lambda item: item.relative)
        ],
        "tested_voice_package": tested_voice,
        "omitted": [
            "Python interpreter and venv metadata (created on the target machine)",
            "pip, setuptools, wheel and package dist-info metadata",
            "CUDA headers, import/static libraries and development files",
            "cuRAND, cuSPARSE, cuSOLVER, NVRTC, nvJitLink and NVTX runtime packages",
            "ONNX Runtime TensorRT provider and transformer/quantization helpers",
        ],
    }
    try:
        validate_bundle_manifest(manifest)
    except CudaBundleError as exc:
        raise BundleBuildError(str(exc)) from exc

    sums = "".join(f"{item.sha256}  {item.relative}\n" for item in sorted(payload, key=lambda item: item.relative))
    manifest_bytes = _canonical_json(manifest)
    notices = _notices(source_root, payload)
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".building")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        _write_member_bytes(archive, "bundle.json", manifest_bytes, zipfile.ZIP_DEFLATED)
        _write_member_bytes(archive, "SHA256SUMS.txt", sums.encode("ascii"), zipfile.ZIP_DEFLATED)
        _write_member_bytes(archive, "THIRD_PARTY_NOTICES.txt", notices, zipfile.ZIP_DEFLATED)
        for item in sorted(payload, key=lambda item: item.relative):
            archive_name = f"{contract.CUDA_RUNTIME_BUNDLE_PAYLOAD_ROOT}/{item.relative}"
            _write_member_from_file(archive, archive_name, item.source, zipfile.ZIP_DEFLATED)
    temporary.replace(output)
    archive_hash = sha256_file(output)
    release_metadata = {
        "archive_name": output.name,
        "archive_bytes": output.stat().st_size,
        "archive_sha256": archive_hash,
        "payload_bytes": sum(item.size for item in payload),
        "payload_files": len(payload),
        "bundle_id": bundle_id,
        "manifest": manifest,
    }
    summary = {"archive": str(output), **release_metadata}
    sidecar = output.with_name(output.name + ".release.json")
    sidecar.write_bytes(_canonical_json(release_metadata))
    return summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True, help="pip CUDA venv root")
    parser.add_argument("--output", type=Path, required=True, help="output ZIP path")
    parser.add_argument("--voice-package", type=Path, help="optional tested ONNX package root")
    parser.add_argument("--list-only", action="store_true", help="validate and list files without writing ZIP")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        payload = collect_payload(args.source_root)
        if args.list_only:
            for item in sorted(payload, key=lambda item: item.relative):
                print(f"{item.relative}\t{item.size}\t{item.sha256}")
            print(f"payload files: {len(payload)}; bytes: {sum(item.size for item in payload)}")
            return 0
        summary = build_bundle(
            args.source_root,
            args.output,
            voice_package_root=args.voice_package,
        )
    except (BundleBuildError, OSError, zipfile.BadZipFile) as exc:
        print(f"[cuda-bundle] error: {exc}", file=sys.stderr)
        return 2
    print(
        f"[cuda-bundle] wrote {summary['archive']} "
        f"({summary['archive_bytes']} bytes, sha256={summary['archive_sha256']})"
    )
    print(f"[cuda-bundle] payload: {summary['payload_files']} files / {summary['payload_bytes']} bytes")
    print(f"[cuda-bundle] release metadata: {summary['archive']}.release.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
