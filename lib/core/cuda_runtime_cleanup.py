"""Bounded cleanup for obsolete CUDA voice runtime artifacts."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import voice_runtime_contract as contract


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


def cleanup_obsolete_cuda_runtime_artifacts(
    runtime_parent: Path | None = None,
    *,
    preserve_valid_runtime: bool = True,
) -> CudaRuntimeCleanupReport:
    """Remove only direct children of the managed ``onnx-cuda`` directory.

    Reparse points are deliberately left untouched. The function never scans
    outside the fixed runtime parent and preserves the current pinned Bundle
    when requested.
    """

    parent = (
        Path(runtime_parent)
        if runtime_parent is not None
        else contract.get_cuda_runtime_root().parent
    )
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

    current_root = contract.get_cuda_runtime_root()
    try:
        current_name = current_root.name.casefold()
    except Exception:
        current_name = ""

    removed: list[Path] = []
    skipped: list[Path] = []
    errors: list[str] = []
    try:
        children = tuple(parent.iterdir())
    except OSError as exc:
        return CudaRuntimeCleanupReport(errors=(f"无法枚举 CUDA 运行目录：{exc}",))

    for candidate in children:
        name = candidate.name
        lower_name = name.casefold()
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

        should_remove = False
        try:
            if candidate.is_dir():
                is_current = lower_name == current_name
                if (
                    is_current
                    and preserve_valid_runtime
                    and contract.is_cuda_runtime_ready(candidate)
                ):
                    continue
                should_remove = True
            elif candidate.is_file():
                should_remove = (
                    lower_name.endswith(".part")
                    or lower_name.endswith(".zip")
                    or lower_name.endswith(".zip.download")
                )
        except OSError as exc:
            errors.append(f"无法检查 {candidate}：{exc}")
            continue

        if not should_remove:
            continue
        try:
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                os.unlink(candidate)
            removed.append(candidate)
        except OSError as exc:
            errors.append(f"清理 {candidate} 失败：{exc}")

    return CudaRuntimeCleanupReport(
        removed=tuple(removed),
        skipped=tuple(skipped),
        errors=tuple(errors),
    )


__all__ = [
    "CudaRuntimeCleanupReport",
    "cleanup_obsolete_cuda_runtime_artifacts",
]
