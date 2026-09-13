"""退出主程序后覆盖安装更新包并重新启动桌宠。"""

from __future__ import annotations

import hashlib
import io
import json
import lzma
import os
import shutil
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

# 在线资源包与离线安装器共用同一套 LZMA2 分片布局（构建方见
# scripts/build_offline_installer.py，原生解码方见
# installer/windows/src/zip_extract.h）。LTS1.0.7pre4 起应用内更新器同时读分片
# 归档和旧的普通 Deflate ZIP：在线资源包先继续发布 Deflate 版本作为切换缓冲，
# 等所有在用客户端都能读分片后再由构建脚本改用分片布局。
RESOURCE_ARCHIVE_MARKER = ".fsv-install-root"
RESOURCE_SHARD_INDEX_NAME = ".fsv-shard-index.bin"
RESOURCE_SHARD_NAME_PREFIX = ".fsv-shard-"
RESOURCE_SHARD_NAME_SUFFIX = ".fsvlzma"
RESOURCE_SHARD_INDEX_ROW_FORMAT = struct.Struct("<IQQ")
RESOURCE_SHARD_DICT_SIZE = 64 << 20
RESOURCE_SHARD_FILTERS = (
    {"id": lzma.FILTER_LZMA2, "dict_size": RESOURCE_SHARD_DICT_SIZE},
)
_RESOURCE_SHARD_READ_SIZE = 1 << 20


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
    # The payload bytes live in LZMA2 shard entries, which are ordinary stored
    # entries, while every payload path keeps a placeholder entry that only
    # advertises its real size.  Both are covered below: the loop still vets
    # every path, and ``testzip`` still verifies the shards' CRC-32, while the
    # zero-length placeholders cost nothing to read.
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


def extract_update_installer_bundle(bundle_path: Path, destination: Path) -> Path:
    """Extract the single native installer from an outer distribution ZIP."""
    bundle = Path(bundle_path).resolve()
    target_root = Path(destination).resolve()
    if not bundle.is_file():
        raise ValueError("更新安装器压缩包不存在")
    try:
        with zipfile.ZipFile(bundle, "r") as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) != 1:
                raise ValueError("更新安装器压缩包必须只包含一个文件")
            member = members[0]
            normalized = str(member.filename or "").replace("\\", "/")
            member_path = Path(normalized)
            if (
                not normalized
                or member_path.is_absolute()
                or ".." in member_path.parts
                or member_path.suffix.casefold() != ".exe"
            ):
                raise ValueError("更新安装器压缩包包含不安全或无效文件")
            target_root.mkdir(parents=True, exist_ok=True)
            target = target_root / member_path.name
            with archive.open(member, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            return target
    except zipfile.BadZipFile as exc:
        raise ValueError(f"更新安装器压缩包不是有效 ZIP：{exc}") from exc


def install_resource_bundle(bundle_path: Path, project_root: Path) -> None:
    """Safely overlay a desktop/runtime resource ZIP onto an installation.

    LTS1.0.7pre4 起同时接受旧的普通 Deflate 资源包和新的 LZMA2 分片资源包：归档里
    带分片索引就走分片解码，否则回退到 ``zipfile`` 逐条解压。
    """
    bundle = Path(bundle_path).resolve()
    target_root = _installation_root(Path(project_root))
    staging = bundle.parent / f".fsv-resource-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(bundle) as archive:
            _extract_resource_archive(archive, staging)
        marker = staging / RESOURCE_ARCHIVE_MARKER
        if not marker.is_file():
            raise ValueError("资源包缺少安装标记")
        for source in staging.iterdir():
            if source.name == ".fsv-install-root":
                continue
            destination = target_root / source.name
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _resource_archive_name(member: zipfile.ZipInfo) -> str:
    return str(member.filename or "").replace("\\", "/")


def _resource_member_path(name: str) -> Path:
    path = Path(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"资源包包含不安全路径：{name}")
    return path


def _is_resource_shard_name(name: str) -> bool:
    return name.startswith(RESOURCE_SHARD_NAME_PREFIX) and name.endswith(
        RESOURCE_SHARD_NAME_SUFFIX
    )


def _read_resource_shard_index(
    archive: zipfile.ZipFile,
) -> list[tuple[int, int, int]] | None:
    """按中央目录顺序返回 ``(分片号, 分片内偏移, 长度)``；普通 ZIP 返回 None。"""
    try:
        raw = archive.read(RESOURCE_SHARD_INDEX_NAME)
    except KeyError:
        return None
    if len(raw) < 4:
        raise ValueError("资源包分片索引不完整")
    count = struct.unpack_from("<I", raw, 0)[0]
    row_size = RESOURCE_SHARD_INDEX_ROW_FORMAT.size
    if len(raw) < 4 + count * row_size:
        raise ValueError("资源包分片索引不完整")
    return [
        RESOURCE_SHARD_INDEX_ROW_FORMAT.unpack_from(raw, 4 + index * row_size)
        for index in range(count)
    ]


class _ResourceShardReader:
    """把一条 raw LZMA2 分片流按需解码成有界大小的输出块。"""

    def __init__(
        self, stream: io.BufferedIOBase, *, chunk_size: int = _RESOURCE_SHARD_READ_SIZE
    ) -> None:
        self._stream = stream
        self._chunk_size = int(chunk_size)
        self._decompressor = lzma.LZMADecompressor(
            format=lzma.FORMAT_RAW, filters=RESOURCE_SHARD_FILTERS
        )
        self._buffer = b""
        self._input_done = False
        self.position = 0

    def _pump(self) -> bool:
        if self._decompressor.eof:
            return False
        if self._decompressor.needs_input:
            if self._input_done:
                return False
            chunk = self._stream.read(self._chunk_size)
            if not chunk:
                self._input_done = True
            data = self._decompressor.decompress(chunk, self._chunk_size)
        else:
            data = self._decompressor.decompress(b"", self._chunk_size)
        if not data:
            return False
        self._buffer += data
        return True

    def read(self, count: int) -> bytes:
        while len(self._buffer) < count:
            if not self._pump():
                break
        data = self._buffer[:count]
        self._buffer = self._buffer[count:]
        self.position += len(data)
        return data

    def skip_to(self, offset: int) -> bool:
        if offset < self.position:
            return False
        while self.position < offset:
            chunk = self.read(min(offset - self.position, self._chunk_size))
            if not chunk:
                return False
        return True

    def verify_complete(self) -> bool:
        """读到流结束标记，并在有多余解压数据时判定索引与归档不一致。"""
        while True:
            if self._decompressor.eof:
                return not self._buffer
            if self._buffer:
                return False
            if not self._pump():
                return False


def _extract_resource_shard_member(
    reader: _ResourceShardReader, target: Path, size: int
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    remaining = int(size)
    with target.open("wb") as output:
        while remaining > 0:
            chunk = reader.read(min(remaining, _RESOURCE_SHARD_READ_SIZE))
            if not chunk:
                raise ValueError("资源包分片数据不完整")
            output.write(chunk)
            remaining -= len(chunk)


def _extract_sharded_resource_archive(
    archive: zipfile.ZipFile,
    staging: Path,
    rows: list[tuple[int, int, int]],
) -> None:
    placeholders: list[tuple[zipfile.ZipInfo, str]] = []
    shard_names: set[str] = set()
    for member in archive.infolist():
        name = _resource_archive_name(member)
        if name == RESOURCE_SHARD_INDEX_NAME:
            continue
        if _is_resource_shard_name(name):
            shard_names.add(name)
            continue
        _resource_member_path(name)
        if member.is_dir() or name == RESOURCE_ARCHIVE_MARKER:
            archive.extract(member, staging)
            continue
        placeholders.append((member, name))
    if len(placeholders) != len(rows):
        raise ValueError("资源包分片索引与文件数量不一致")
    shards: dict[int, list[tuple[int, int, Path]]] = {}
    for (member, name), (shard, offset, size) in zip(placeholders, rows):
        if member.file_size not in (0, size):
            raise ValueError(f"资源包分片索引与文件大小不一致：{name}")
        target = staging / _resource_member_path(name)
        shards.setdefault(shard, []).append((offset, size, target))
    for shard in sorted(shards):
        shard_name = f"{RESOURCE_SHARD_NAME_PREFIX}{shard:03d}{RESOURCE_SHARD_NAME_SUFFIX}"
        if shard_name not in shard_names:
            raise ValueError(f"资源包缺少分片：{shard_name}")
        with archive.open(shard_name, "r") as stream:
            reader = _ResourceShardReader(stream)
            for offset, size, target in sorted(shards[shard]):
                if not reader.skip_to(offset):
                    raise ValueError(f"资源包分片偏移超出范围：{shard_name}")
                _extract_resource_shard_member(reader, target, size)
            if not reader.verify_complete():
                raise ValueError(f"资源包分片数据不完整：{shard_name}")


def _extract_resource_archive(archive: zipfile.ZipFile, staging: Path) -> None:
    """把资源包解到 staging；分片归档与旧的 Deflate 归档走不同路径。"""
    rows = _read_resource_shard_index(archive)
    if rows is None:
        for member in archive.infolist():
            _resource_member_path(_resource_archive_name(member))
            archive.extract(member, staging)
        return
    _extract_sharded_resource_archive(archive, staging, rows)


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
