"""Resolve the official UnRAR backend shipped with the desktop pet."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable


UNRAR_SHA256 = "0d3715001790f0fd18d3e850f947b540530b2d2deb9a2e6a9e84f2ed7b234235"
UNRAR_BYTES = 560_848

ProgressCallback = Callable[[int, int], None]


class RarBackendError(RuntimeError):
    pass


def get_bundled_unrar_dir() -> Path:
    return Path(__file__).resolve().parent / "bin"


def get_bundled_unrar_path() -> Path:
    return get_bundled_unrar_dir() / "UnRAR.exe"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(256 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest().lower()


def is_bundled_unrar_ready() -> bool:
    path = get_bundled_unrar_path()
    try:
        return (
            path.is_file()
            and path.stat().st_size == UNRAR_BYTES
            and _sha256(path) == UNRAR_SHA256
        )
    except OSError:
        return False


def ensure_bundled_unrar(
    progress_callback: ProgressCallback | None = None,
) -> Path:
    path = get_bundled_unrar_path()
    ready = is_bundled_unrar_ready()
    if progress_callback is not None:
        progress_callback(UNRAR_BYTES if ready else 0, UNRAR_BYTES)
    if not ready:
        raise RarBackendError("随程序提供的 UnRAR.exe 缺失或校验失败，请重新解压桌宠程序包")
    return path
