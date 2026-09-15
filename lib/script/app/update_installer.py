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
from typing import Callable, Sequence

from config.user_storage_paths import get_user_state_dir
from lib.script.app.restart import (
    _detached_kwargs,
)
from lib.script.app.update_paths import canonical_path

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

# 覆盖安装时被扫盘、杀毒或索引进程短暂占住的目标文件不立刻判失败：先把能替换的
# 全部替换掉，被占用的记下来最后回头重试（``RESOURCE_OVERLAY_ATTEMPTS`` 次、
# 间隔 ``RESOURCE_OVERLAY_RETRY_DELAY`` 秒），仍然失败的登记为待补装项。
RESOURCE_OVERLAY_ATTEMPTS = 4
RESOURCE_OVERLAY_RETRY_DELAY = 0.5
# 覆盖前比对同名文件内容用的读数块大小。
_OVERLAY_COMPARE_SIZE = 1 << 20

# 一次资源包覆盖里没能替换掉的少数文件（基本只有桌宠自己加载中的模块）会搬到用户根的
# 待补装目录，由下次启动时最先执行的 ``apply_pending_overlay`` 补上：那时桌宠还没加载
# 这些模块，文件已经可以替换，模块与它依赖的 DLL 也就不会被换出半个版本。
PENDING_OVERLAY_VERSION = 1
PENDING_OVERLAY_DIR_NAME = "update-pending-overlay"
PENDING_OVERLAY_MANIFEST_NAME = "manifest.json"
PENDING_OVERLAY_FILES_NAME = "files"

# 校验过的离线安装器按「路径 + 大小 + mtime」缓存：一次更新里安装器会被校验两遍
# （下载后交接前、真正启动前），第二遍必须零成本。
_INSTALLER_CACHE: dict[tuple[str, int, int], OfflineInstallerInfo] = {}


@dataclass(frozen=True)
class OfflineInstallerInfo:
    path: Path
    archive_offset: int
    archive_size: int
    archive_sha256: str


@dataclass(frozen=True)
class OverlayOutcome:
    """资源包覆盖安装的结果。

    ``locked`` 是被占用、没能替换的相对路径；``staging`` 仍保留解压结果，交给
    ``defer_overlay_leftovers`` 搬进待补装目录，没有待补装项时为 None。
    """

    locked: tuple[str, ...] = ()
    staging: Path | None = None
    target_root: Path | None = None


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


def _installer_cache_key(installer: Path) -> tuple[str, int, int]:
    stat = installer.stat()
    return (str(installer), int(stat.st_size), int(stat.st_mtime_ns))


def clear_update_installer_cache() -> None:
    """清空安装器校验缓存（测试改写同一个文件时使用）。"""
    _INSTALLER_CACHE.clear()


def _hash_file_range(path: Path, offset: int, size: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        handle.seek(offset)
        remaining = int(size)
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("离线安装器内置归档提前结束")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def validate_update_installer(
    installer_path: Path, *, verify_payload: bool = False
) -> OfflineInstallerInfo:
    """校验离线安装器，并返回它的 PE 尾记录。

    更新链路的完整性只认一个哈希：发布清单里的 SHA-256，它在下载时随流算出（见
    ``UpdateManager._download_url``）。``verify_payload`` 只留给清单没给哈希的
    情况，那时才把内置归档再哈希一遍。

    ZIP 目录这里只做结构与路径检查，不再调 ``testzip()``：那会把几百兆 payload
    整份解压并逐条算 CRC-32，是整个更新流程里最卡的一步，而真正落盘的解压由原生
    安装器完成。校验结果按“路径 + 大小 + mtime”缓存，同一次更新里安装器被校验
    两遍（交接前、启动前）不会重复读盘。
    """
    installer = Path(installer_path).resolve()
    if not installer.is_file():
        raise ValueError("离线安装器不存在")
    key = _installer_cache_key(installer)
    cached = _INSTALLER_CACHE.get(key)
    if cached is not None:
        return cached
    info = _read_offline_installer_trailer(installer)
    if verify_payload and _hash_file_range(
        info.path, info.archive_offset, info.archive_size
    ).casefold() != info.archive_sha256.casefold():
        raise ValueError("离线安装器内置归档 SHA-256 校验失败")

    # 通过有界文件视图校验 ZIP 目录。原生安装器会在真正解压时做最终检查，这里提前
    # 拒绝畸形下载，好在退出桌宠之前给出可操作的错误。
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
    except zipfile.BadZipFile as exc:
        raise ValueError(f"离线安装器内置归档不是有效 ZIP：{exc}") from exc
    _INSTALLER_CACHE[key] = info
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


def install_resource_bundle(
    bundle_path: Path,
    project_root: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> OverlayOutcome:
    """Safely overlay a desktop/runtime resource ZIP onto an installation.

    LTS1.0.7pre4 起同时接受旧的普通 Deflate 资源包和新的 LZMA2 分片资源包：归档里
    带分片索引就走分片解码，否则回退到 ``zipfile`` 逐条解压。

    返回值列出「内容确实不同、但文件被占用而没能替换」的相对路径。调用方（
    ``UpdateManager.install_release``）会把它们登记成待补装项，而不是让整次更新失败：
    在线资源包里绝大多数文件与安装目录逐字节相同，剩下一两个被加载中的模块本来就要等
    桌宠重启才能换。
    """
    report = progress if callable(progress) else (lambda _message: None)
    bundle = Path(bundle_path).resolve()
    target_root = _installation_root(Path(project_root))
    staging = bundle.parent / f".fsv-resource-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        report("正在解压资源包…")
        with zipfile.ZipFile(bundle) as archive:
            _extract_resource_archive(archive, staging)
        marker = staging / RESOURCE_ARCHIVE_MARKER
        if not marker.is_file():
            raise ValueError("资源包缺少安装标记")
        report("正在覆盖安装文件…")
        locked = _overlay_staging(staging, target_root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if not locked:
        shutil.rmtree(staging, ignore_errors=True)
        return OverlayOutcome(target_root=target_root)
    return OverlayOutcome(
        locked=tuple(
            sorted(
                destination.relative_to(target_root).as_posix()
                for _, destination in locked
            )
        ),
        staging=staging,
        target_root=target_root,
    )


def _file_digest(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(_OVERLAY_COMPARE_SIZE), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _same_content(source: Path, destination: Path) -> bool:
    """包内文件与安装目录里的同名文件是否逐字节相同。"""
    try:
        if not destination.is_file():
            return False
        if destination.stat().st_size != source.stat().st_size:
            return False
    except OSError:
        return False
    source_digest = _file_digest(source)
    return source_digest is not None and source_digest == _file_digest(destination)


def _overlay_actions(
    actions: list[tuple[Path | None, Path]],
) -> list[tuple[Path | None, Path]]:
    """执行覆盖动作，只重试失败的那些；返回最终仍然失败的动作。"""
    pending = list(actions)
    for attempt in range(RESOURCE_OVERLAY_ATTEMPTS):
        failed: list[tuple[Path | None, Path]] = []
        for source, destination in pending:
            try:
                if source is None:
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            except OSError:
                failed.append((source, destination))
        if not failed:
            return []
        pending = failed
        if attempt + 1 < RESOURCE_OVERLAY_ATTEMPTS:
            time.sleep(RESOURCE_OVERLAY_RETRY_DELAY)
    return pending


def _overlay_staging(
    staging: Path, target_root: Path
) -> list[tuple[Path | None, Path]]:
    """把 staging 的内容覆盖到安装目录，返回被占用、没能替换的文件。

    逐文件替换而不是整目录 ``copytree``，是为了让一个被占用的文件只跳过它自己，
    目录里其它文件照常更新。目录本身也走同一套重试，空的目录结构因此不会丢。
    内容与安装目录逐字节相同的文件直接跳过：既省掉一次全量写入，也不会去撞别人
    已经打开的文件句柄。
    """
    actions: list[tuple[Path | None, Path]] = []
    for source in sorted(staging.iterdir()):
        if source.name == RESOURCE_ARCHIVE_MARKER:
            continue
        destination = target_root / source.name
        if not source.is_dir():
            if not _same_content(source, destination):
                actions.append((source, destination))
            continue
        actions.append((None, destination))
        for item in sorted(source.rglob("*")):
            relative = item.relative_to(source)
            if item.is_dir():
                actions.append((None, destination / relative))
            elif not _same_content(item, destination / relative):
                actions.append((item, destination / relative))
    return _overlay_actions(actions)


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


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


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


def pending_overlay_root() -> Path:
    """待补装目录：存放上次覆盖安装里没能替换、等下次启动补上的文件。"""
    return get_user_state_dir(PENDING_OVERLAY_DIR_NAME)


def _relative_overlay_path(value: object) -> str | None:
    """把登记的相对路径规整成 posix 写法；不安全或为空时返回 None。"""
    raw = str(value or "").replace("\\", "/").strip()
    if not raw:
        return None
    try:
        _resource_member_path(raw)
    except ValueError:
        return None
    return raw


def _load_pending_overlay(manifest_path: Path) -> dict:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("version") != PENDING_OVERLAY_VERSION:
        return {}
    files = payload.get("files")
    entries: dict[str, dict] = {}
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            relative = _relative_overlay_path(item.get("path"))
            if relative is not None:
                entries[relative] = item
    return {
        "version": PENDING_OVERLAY_VERSION,
        "install_root": str(payload.get("install_root") or ""),
        "release": payload.get("release") if isinstance(payload.get("release"), dict) else {},
        "files": entries,
    }


def _pending_overlay_signature(entry: dict) -> tuple[int, int] | None:
    """补装登记时记下的「大小 + mtime」；清单被改坏时返回 None。"""
    size = entry.get("size")
    mtime_ns = entry.get("mtime_ns")
    if size is None or mtime_ns is None:
        return None
    try:
        return int(size), int(mtime_ns)
    except (TypeError, ValueError):
        return None


def _write_pending_overlay(manifest: dict) -> None:
    root = pending_overlay_root()
    manifest_path = root / PENDING_OVERLAY_MANIFEST_NAME
    entries = manifest.get("files") or {}
    if not entries:
        shutil.rmtree(root, ignore_errors=True)
        return
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": PENDING_OVERLAY_VERSION,
        "install_root": str(manifest.get("install_root") or ""),
        "release": manifest.get("release") or {},
        "files": [entries[name] for name in sorted(entries)],
    }
    temporary = manifest_path.with_name(manifest_path.name + ".part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, manifest_path)


def defer_overlay_leftovers(
    outcome: OverlayOutcome,
    target_root: Path,
    *,
    release: dict | None = None,
) -> tuple[str, ...]:
    """把没能替换的文件搬进待补装目录，登记给下次启动补上。

    登记时记下目标文件当时的「大小 + mtime」：如果之后有别的流程（比如完整离线安装器）
    换过这个文件，补装就会跳过它，不会用旧字节覆盖更新的版本。
    """
    staging = Path(outcome.staging) if outcome.staging is not None else None
    deferred: list[str] = []
    try:
        if staging is None or not staging.is_dir():
            return ()
        root = pending_overlay_root()
        files_root = root / PENDING_OVERLAY_FILES_NAME
        manifest_path = root / PENDING_OVERLAY_MANIFEST_NAME
        manifest = _load_pending_overlay(manifest_path)
        if manifest:
            recorded_root = Path(manifest.get("install_root") or ".")
            if canonical_path(recorded_root) != canonical_path(target_root):
                # 上一次的待补装项属于另一个安装目录：连同暂存文件一起丢弃，
                # 不让它们永远占着用户根。
                shutil.rmtree(root, ignore_errors=True)
                manifest = {}
        manifest.setdefault("version", PENDING_OVERLAY_VERSION)
        manifest["install_root"] = str(target_root)
        if release:
            manifest["release"] = dict(release)
        entries = manifest.setdefault("files", {})
        for relative in outcome.locked:
            normalized = _relative_overlay_path(relative)
            if normalized is None:
                continue
            source = staging / Path(normalized)
            if not source.is_file():
                continue
            destination = files_root / Path(normalized)
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
            except OSError:
                try:
                    shutil.copy2(source, destination)
                except OSError:
                    continue
            try:
                stat = (Path(target_root) / Path(normalized)).stat()
                size: int | None = int(stat.st_size)
                mtime_ns: int | None = int(stat.st_mtime_ns)
            except OSError:
                size, mtime_ns = None, None
            entries[normalized] = {
                "path": normalized,
                "size": size,
                "mtime_ns": mtime_ns,
            }
            deferred.append(normalized)
        if not deferred:
            return ()
        _write_pending_overlay(manifest)
        return tuple(sorted(deferred))
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def apply_pending_overlay(install_root: Path | None = None) -> tuple[str, ...]:
    """启动时补装上次更新里被占用、没能替换的文件；失败静默留给下次启动。

    必须在桌宠加载这些模块之前运行（见 ``lib/core/qt_desktop_pet.py`` 的启动钩子），
    这样被换掉的模块与它依赖的 DLL 才会同时是最新的一份。
    """
    manifest_path = pending_overlay_root() / PENDING_OVERLAY_MANIFEST_NAME
    if not manifest_path.is_file():
        return ()
    manifest = _load_pending_overlay(manifest_path)
    entries = manifest.get("files") or {}
    if not entries:
        _write_pending_overlay({"files": {}})
        return ()
    root = (
        Path(install_root).resolve()
        if install_root is not None
        else _installation_root(_PROJECT_ROOT)
    )
    if manifest.get("install_root") and canonical_path(
        manifest["install_root"]
    ) != canonical_path(root):
        _write_pending_overlay({"files": {}})
        return ()
    files_root = pending_overlay_root() / PENDING_OVERLAY_FILES_NAME
    applied: list[str] = []
    remaining: dict[str, dict] = {}
    for relative, entry in entries.items():
        source = files_root / Path(relative)
        target = root / Path(relative)
        if not source.is_file():
            continue
        try:
            stat = target.stat()
            current = (int(stat.st_size), int(stat.st_mtime_ns))
        except OSError:
            current = None
        recorded = _pending_overlay_signature(entry)
        if recorded is not None and current != recorded:
            # 安装目录里这个文件已经被别的流程换过，补装不再适用。
            source.unlink(missing_ok=True)
            continue
        if current is not None and _same_content(source, target):
            source.unlink(missing_ok=True)
            applied.append(relative)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError:
            remaining[relative] = entry
            continue
        source.unlink(missing_ok=True)
        applied.append(relative)
    _write_pending_overlay(
        {
            "install_root": str(root),
            "release": manifest.get("release") or {},
            "files": remaining,
        }
    )
    return tuple(sorted(applied))


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
