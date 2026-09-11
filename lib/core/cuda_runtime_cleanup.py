"""Bounded cleanup for the obsolete onnx-cuda voice runtime artifacts.

Earlier releases downloaded a multi-gigabyte CUDA ORT bundle into
``<shared>/voice/runtimes/onnx-cuda``.  The self-written runtime needs none of
it, so every startup removes whatever is still there.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class CudaRuntimeCleanupReport:
    removed: tuple[Path, ...] = ()
    skipped: tuple[Path, ...] = ()
    errors: tuple[str, ...] = ()


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0) or 0)
    except OSError:
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _is_managed_runtime_parent(path: Path) -> bool:
    parts = tuple(part.casefold() for part in Path(path).parts)
    return len(parts) >= 3 and parts[-3:] == ("voice", "runtimes", "onnx-cuda")


def _shared_root_dir() -> Path:
    override = str(os.environ.get("AEMEATH_DESK_PET_HOME", "") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2]


def default_runtime_parent() -> Path:
    return _shared_root_dir() / "voice" / "runtimes" / "onnx-cuda"


def cleanup_obsolete_cuda_runtime_artifacts(
    runtime_parent: Path | None = None,
) -> CudaRuntimeCleanupReport:
    """Remove every direct child of the managed ``onnx-cuda`` directory.

    Reparse points are deliberately left untouched and the function never scans
    outside the fixed runtime parent.
    """

    parent = Path(runtime_parent) if runtime_parent is not None else default_runtime_parent()
    parent = parent.expanduser()
    if not _is_managed_runtime_parent(parent):
        return CudaRuntimeCleanupReport(errors=(f"拒绝清理非托管目录：{parent}",))
    if not parent.exists():
        return CudaRuntimeCleanupReport()
    if not parent.is_dir() or _is_reparse_point(parent):
        return CudaRuntimeCleanupReport(skipped=(parent,))

    try:
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        return CudaRuntimeCleanupReport(errors=(f"无法解析 CUDA 运行目录：{exc}",))

    removed: list[Path] = []
    skipped: list[Path] = []
    errors: list[str] = []
    try:
        children = tuple(parent.iterdir())
    except OSError as exc:
        return CudaRuntimeCleanupReport(errors=(f"无法枚举 CUDA 运行目录：{exc}",))

    for candidate in children:
        if _is_reparse_point(candidate) or candidate.is_symlink():
            skipped.append(candidate)
            continue
        try:
            resolved = candidate.resolve(strict=False)
            if resolved.parent != resolved_parent:
                skipped.append(candidate)
                continue
        except OSError as exc:
            errors.append(f"无法解析 {candidate}：{exc}")
            continue
        try:
            if candidate.is_dir():
                shutil.rmtree(candidate)
            elif candidate.is_file():
                os.unlink(candidate)
            else:
                continue
        except OSError as exc:
            errors.append(f"清理 {candidate} 失败：{exc}")
            continue
        removed.append(candidate)

    return CudaRuntimeCleanupReport(
        removed=tuple(removed),
        skipped=tuple(skipped),
        errors=tuple(errors),
    )


__all__ = [
    "CudaRuntimeCleanupReport",
    "cleanup_obsolete_cuda_runtime_artifacts",
    "default_runtime_parent",
]
