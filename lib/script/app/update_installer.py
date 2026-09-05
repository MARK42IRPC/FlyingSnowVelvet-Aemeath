"""退出主程序后覆盖安装更新包并重新启动桌宠。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import struct
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from lib.script.app.restart import (
    _detached_kwargs,
)

# Keep this protocol in sync with scripts/build_offline_installer.py and the
# native installer.  The trailer lets the updater validate a downloaded EXE
# without trusting a filename or loading the complete payload into memory.
OFFLINE_INSTALLER_MAGIC = b"FSV-OFFLINE-PAYLOAD-2"
OFFLINE_INSTALLER_TRAILER_FORMAT = "<24sQ32s"
OFFLINE_INSTALLER_TRAILER_SIZE = struct.calcsize(OFFLINE_INSTALLER_TRAILER_FORMAT)


@dataclass(frozen=True)
class OfflineInstallerInfo:
    path: Path
    archive_offset: int
    archive_size: int
    archive_sha256: str


class _BoundedFile:
    """A seekable view over the appended ZIP portion of an installer."""

    def __init__(self, handle: io.BufferedReader, start: int, size: int) -> None:
        self._handle = handle
        self._start = int(start)
        self._size = int(size)
        self._position = 0

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = int(offset)
        elif whence == io.SEEK_CUR:
            position = self._position + int(offset)
        elif whence == io.SEEK_END:
            position = self._size + int(offset)
        else:
            raise ValueError("invalid whence")
        if position < 0:
            raise ValueError("negative seek position")
        self._position = min(position, self._size)
        self._handle.seek(self._start + self._position, io.SEEK_SET)
        return self._position

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self._size - self._position
        size = min(int(size), self._size - self._position)
        if size <= 0:
            return b""
        self._handle.seek(self._start + self._position, io.SEEK_SET)
        data = self._handle.read(size)
        self._position += len(data)
        return data

    def close(self) -> None:
        # ZipFile must not close the parent handle; the parent owns it.
        return None

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True


def _read_offline_installer_trailer(path: Path) -> OfflineInstallerInfo:
    installer = Path(path).resolve()
    if not installer.is_file():
        raise ValueError("离线安装器不存在")
    file_size = installer.stat().st_size
    if file_size < OFFLINE_INSTALLER_TRAILER_SIZE + 2:
        raise ValueError("离线安装器文件不完整")
    with installer.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise ValueError("下载文件不是 Windows 安装器")
        handle.seek(-OFFLINE_INSTALLER_TRAILER_SIZE, io.SEEK_END)
        raw = handle.read(OFFLINE_INSTALLER_TRAILER_SIZE)
    try:
        magic, archive_size, expected_hash = struct.unpack(
            OFFLINE_INSTALLER_TRAILER_FORMAT, raw
        )
    except struct.error as exc:
        raise ValueError("离线安装器尾记录无效") from exc
    if magic.rstrip(b"\0") != OFFLINE_INSTALLER_MAGIC:
        raise ValueError("离线安装器版本不受支持")
    if archive_size <= 0 or archive_size > file_size - OFFLINE_INSTALLER_TRAILER_SIZE:
        raise ValueError("离线安装器内置归档长度无效")
    archive_offset = file_size - OFFLINE_INSTALLER_TRAILER_SIZE - archive_size
    return OfflineInstallerInfo(
        path=installer,
        archive_offset=archive_offset,
        archive_size=archive_size,
        archive_sha256=expected_hash.hex(),
    )


def validate_update_installer(installer_path: Path) -> OfflineInstallerInfo:
    """Validate an offline installer and its appended ZIP payload.

    Validation is deliberately streaming so a several-hundred-megabyte
    release cannot exhaust the desktop process while an update is prepared.
    """
    info = _read_offline_installer_trailer(Path(installer_path))
    digest = hashlib.sha256()
    with info.path.open("rb") as handle:
        handle.seek(info.archive_offset)
        remaining = info.archive_size
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("离线安装器内置归档提前结束")
            digest.update(chunk)
            remaining -= len(chunk)
    if digest.hexdigest().casefold() != info.archive_sha256.casefold():
        raise ValueError("离线安装器内置归档 SHA-256 校验失败")

    # Validate the ZIP directory through a bounded file view.  The native
    # installer performs the final extraction/path checks, but rejecting a
    # malformed download here gives the user an actionable error before exit.
    try:
        with info.path.open("rb") as handle:
            bounded = _BoundedFile(handle, info.archive_offset, info.archive_size)
            with zipfile.ZipFile(bounded, "r") as bundle:
                members = bundle.infolist()
                if not members:
                    raise ValueError("离线安装器内置归档为空")
                for member in members:
                    normalized = str(member.filename or "").replace("\\", "/")
                    member_path = Path(normalized)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError(f"离线安装器包含不安全路径：{member.filename}")
                broken = bundle.testzip()
                if broken:
                    raise ValueError(f"离线安装器内置归档损坏：{broken}")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"离线安装器内置归档不是有效 ZIP：{exc}") from exc
    return info


def _installation_root(project_root: Path) -> Path:
    """Resolve the directory selected by the native installer.

    A packaged process runs from ``<install>/app`` while a source checkout
    runs from the repository root.  Keep both forms deterministic and never
    infer a target from the current working directory.
    """
    root = Path(project_root).resolve()
    if (root / "runtime" / "python311").is_dir():
        return root
    parent = root.parent
    if (parent / "runtime" / "python311").is_dir() and (parent / "app").is_dir():
        return parent
    return root


def _write_pending_release(path: Path, release: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "version": str(release.get("tag") or "latest"),
                "installed_at": str(release.get("published_at") or ""),
                "revision": str(release.get("revision") or ""),
                "source": str(release.get("source") or ""),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def launch_update_installer(
    installer_path: Path,
    project_root: Path,
    state_path: Path,
    release: dict,
    *,
    restart_command: Sequence[str] | None = None,
) -> subprocess.Popen:
    """Launch the downloaded native offline installer in a detached process.

    ``restart_command`` is accepted for API compatibility with older callers;
    the native installer owns the post-install launch and does not execute a
    caller-provided Python or batch command.
    """
    del restart_command
    installer = Path(installer_path).resolve()
    validate_update_installer(installer)
    target_root = _installation_root(Path(project_root))
    state_destination = Path(state_path).resolve()
    pending_state = installer.parent / f".fsv-release-{os.getpid()}-{int(time.time() * 1000)}.json"
    _write_pending_release(pending_state, release)
    command = [
        str(installer),
        "--update-target",
        str(target_root),
        "--update-state",
        str(state_destination),
        "--update-state-source",
        str(pending_state),
    ]
    try:
        return subprocess.Popen(command, **_detached_kwargs())
    except OSError:
        return subprocess.Popen(
            command,
            **_detached_kwargs(include_breakaway=False),
        )
