"""Contract tests for the sharded LZMA2 payload archive.

The native extractor is exercised by ``tests/test_windows_zip_extract.py``;
these checks pin the on-disk format itself, the constants the C decoder has to
agree with, and the parts of the contract the in-app updater depends on.
"""

from __future__ import annotations

import json
import lzma
import os
import shutil
import struct
import subprocess
import tempfile
import time
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import build_offline_installer as installer
from lib.script.app import update_installer
from lib.script.app.update_installer import (
    apply_pending_overlay,
    defer_overlay_leftovers,
    install_resource_bundle,
)


SHARD_PREFIX = ".fsv-shard-"
SHARD_SUFFIX = ".fsvlzma"


class _FakeSource:
    """Stands in for a payload file whose size alone drives the guard."""

    def __init__(self, size: int) -> None:
        self._size = size

    def stat(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(st_size=self._size)

    def open(self, mode: str):  # pragma: no cover - the guard must fire first
        raise AssertionError(f"oversized payload file must not be read ({mode})")


class ShardedArchiveFormatTests(unittest.TestCase):
    def write_payload(self, root: Path, files: dict[str, bytes]) -> Path:
        payload = root / "payload"
        for name, data in files.items():
            path = payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (payload / installer.MARKER_NAME).write_bytes(installer.MARKER_BYTES)
        return payload

    @staticmethod
    def placeholder_names(bundle: zipfile.ZipFile) -> list[str]:
        return [
            member.filename
            for member in bundle.infolist()
            if member.compress_size == 0 and not member.filename.startswith(SHARD_PREFIX)
        ]

    def test_index_rows_rebuild_every_payload_file(self):
        files = {
            "app/小.txt": b"shard payload " * 11,
            "app/嵌套/大.bin": bytes(range(256)) * 3000,
            "app/空.dat": b"",
        }
        with tempfile.TemporaryDirectory(prefix="fsv-archive-format-") as temporary:
            root = Path(temporary)
            payload = self.write_payload(root, files)
            archive = root / "payload.zip"

            installer.create_archive(payload, archive)

            with zipfile.ZipFile(archive) as bundle:
                self.assertIsNone(bundle.testzip())
                rows = bundle.read(installer.PAYLOAD_SHARD_INDEX_NAME)
                self.assertEqual(struct.unpack_from("<I", rows, 0)[0], len(files))
                self.assertEqual(installer.PAYLOAD_SHARD_INDEX_ROW.size, struct.calcsize("<IQQ"))
                placeholders = self.placeholder_names(bundle)
                # The compatibility view keeps the archive order of the payload.
                self.assertEqual(placeholders, sorted(files))
                decoded = {}
                for member in bundle.infolist():
                    name = member.filename
                    if not (name.startswith(SHARD_PREFIX) and name.endswith(SHARD_SUFFIX)):
                        continue
                    shard = int(name[len(SHARD_PREFIX) : len(SHARD_PREFIX) + 3])
                    decompressor = lzma.LZMADecompressor(
                        format=lzma.FORMAT_RAW,
                        filters=list(installer.PAYLOAD_SHARD_FILTERS),
                    )
                    decoded[shard] = decompressor.decompress(bundle.read(name))
                self.assertTrue(decoded)
                for index, name in enumerate(placeholders):
                    shard, offset, size = installer.PAYLOAD_SHARD_INDEX_ROW.unpack_from(
                        rows, 4 + index * installer.PAYLOAD_SHARD_INDEX_ROW.size
                    )
                    with self.subTest(name=name):
                        self.assertIn(shard, decoded)
                        self.assertEqual(decoded[shard][offset : offset + size], files[name])

    def test_placeholders_advertise_their_real_size(self):
        files = {"app/大.bin": bytes(range(256)) * 2000, "app/小.txt": b"tiny"}
        with tempfile.TemporaryDirectory(prefix="fsv-archive-size-") as temporary:
            root = Path(temporary)
            payload = self.write_payload(root, files)
            archive = root / "payload.zip"

            installer.create_archive(payload, archive)

            raw = archive.read_bytes()
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(
                    set(self.placeholder_names(bundle)),
                    set(files),
                )
                for member in bundle.infolist():
                    if member.filename not in files:
                        continue
                    with self.subTest(name=member.filename):
                        # Both the central directory and the local header have
                        # to carry the size: streaming readers use the latter.
                        self.assertEqual(member.file_size, len(files[member.filename]))
                        self.assertEqual(
                            struct.unpack_from("<I", raw, member.header_offset + 22)[0],
                            len(files[member.filename]),
                        )

    def test_shard_plan_never_splits_a_file_and_fills_shards_in_order(self):
        with tempfile.TemporaryDirectory(prefix="fsv-archive-plan-") as temporary:
            root = Path(temporary)
            payload = self.write_payload(
                root,
                {"app/a.bin": b"a" * 5000, "app/b.bin": b"b" * 4000, "app/c.bin": b"c" * 10},
            )
            entries = [
                (source, relative)
                for source, relative in installer._archive_entries(payload)
                if relative != installer.MARKER_NAME
            ]

            plan = installer._shard_plan(entries)

            self.assertEqual(sorted(member for shard in plan for member in shard), list(range(len(entries))))
            self.assertTrue(all(shard for shard in plan))
            with mock.patch.object(installer, "PAYLOAD_SHARD_TARGET_BYTES", 4096):
                plan = installer._shard_plan(entries)
            # A file larger than the target still gets a shard of its own
            # instead of being split across two streams.
            self.assertEqual(plan[0], [0])

    def test_shard_constants_match_the_native_decoder(self):
        self.assertEqual(
            installer._shard_dictionary_property(installer.PAYLOAD_SHARD_DICT_SIZE),
            installer.PAYLOAD_SHARD_DICT_PROPERTY,
        )
        self.assertEqual(installer.PAYLOAD_SHARD_DICT_PROPERTY, 0x1C)
        self.assertTrue(installer.PAYLOAD_SHARD_NAME_TEMPLATE.startswith(SHARD_PREFIX))
        self.assertTrue(installer.PAYLOAD_SHARD_NAME_TEMPLATE.endswith(SHARD_SUFFIX))
        header = (installer.DEFAULT_INSTALLER_SOURCE / "src" / "zip_extract.h").read_text(
            encoding="utf-8"
        )
        for line in (
            f"#define FSV_ZIP_SHARD_DICT_PROPERTY 0x{installer.PAYLOAD_SHARD_DICT_PROPERTY:02X}",
            f'#define FSV_ZIP_SHARD_INDEX_NAME "{installer.PAYLOAD_SHARD_INDEX_NAME}"',
            f'#define FSV_ZIP_SHARD_NAME_PREFIX "{SHARD_PREFIX}"',
            f'#define FSV_ZIP_SHARD_NAME_SUFFIX "{SHARD_SUFFIX}"',
            f"#define FSV_ZIP_SHARD_INDEX_ROW {installer.PAYLOAD_SHARD_INDEX_ROW.size}U",
        ):
            with self.subTest(line=line):
                self.assertIn(line, header)

    def test_shard_archive_rejects_files_over_four_gibibytes(self):
        with tempfile.TemporaryDirectory(prefix="fsv-archive-limit-") as temporary:
            root = Path(temporary)
            payload = self.write_payload(root, {"app/小.txt": b"tiny"})
            entries = [(_FakeSource(0xFFFFFFFF), "app/巨大.bin")]

            with mock.patch.object(installer, "_archive_entries", return_value=entries):
                with self.assertRaises(SystemExit):
                    installer.create_archive(payload, root / "payload.zip")

    def test_resource_archive_stays_a_plain_deflated_zip(self):
        # LTS1.0.7pre4 的更新器已经能读分片，但更早的客户端只会把占位条目解成
        # 空文件；过渡期内发布的资源包必须继续用 Deflate，直到所有在用客户端
        # 都升到能读分片的版本。
        with tempfile.TemporaryDirectory(prefix="fsv-archive-plain-") as temporary:
            root = Path(temporary)
            payload = self.write_payload(root, {"app/data.bin": b"resource payload" * 512})
            archive = root / "resources.zip"

            installer.create_resource_archive(payload, archive)

            with zipfile.ZipFile(archive) as bundle:
                self.assertIsNone(bundle.testzip())
                names = set(bundle.namelist())
                self.assertNotIn(installer.PAYLOAD_SHARD_INDEX_NAME, names)
                self.assertFalse([name for name in names if name.startswith(SHARD_PREFIX)])
                self.assertEqual(
                    bundle.getinfo("app/data.bin").compress_type, zipfile.ZIP_DEFLATED
                )
                self.assertEqual(bundle.read("app/data.bin"), b"resource payload" * 512)



class PackagingVerificationTests(unittest.TestCase):
    """打包期校验：不逐文件算 SHA-256，改由启动自检 + 两次打包比对哈希。"""

    def staged_workspace(self, root: Path) -> tuple[Path, Path]:
        workspace = root / "workspace"
        payload = workspace / "payload"
        (payload / "app").mkdir(parents=True)
        (payload / "app" / "data.bin").write_bytes(b"payload")
        (workspace / "manifest.json").write_text(
            json.dumps({"version": "LTS-test"}), encoding="utf-8"
        )
        return workspace, payload

    def test_marker_and_manifest_only_record_paths_and_sizes(self):
        with tempfile.TemporaryDirectory(prefix="fsv-marker-") as temporary:
            workspace, payload = self.staged_workspace(Path(temporary))
            with mock.patch.object(installer, "validate_payload", lambda payload: None):
                installer.ensure_payload_marker(workspace, payload)

            manifest_text = (workspace / "manifest.json").read_text(encoding="utf-8")
            self.assertNotIn("sha256", manifest_text)
            entries = {
                entry["path"]: entry
                for entry in json.loads(manifest_text)["files"]
            }
            for path, entry in entries.items():
                with self.subTest(path=path):
                    self.assertEqual(set(entry), {"path", "size"})
            self.assertEqual(
                entries[installer.MARKER_NAME]["size"], len(installer.MARKER_BYTES)
            )
            self.assertEqual(
                entries["app/data.bin"]["size"], (payload / "app" / "data.bin").stat().st_size
            )

    def test_pin_packaged_mtime_overrides_whatever_the_write_left(self):
        with tempfile.TemporaryDirectory(prefix="fsv-pin-") as temporary:
            target = Path(temporary) / "generated.bin"
            target.write_bytes(b"payload")
            os.utime(target, (installer.PACKAGED_FILE_MTIME + 3600,) * 2)
            installer.pin_packaged_mtime(target)
            self.assertEqual(target.stat().st_mtime, installer.PACKAGED_FILE_MTIME)
            # 钉住的时刻同时决定归档条目的时间戳，两次打包才会得到同样的字节。
            self.assertEqual(installer.PACKAGED_FILE_MTIME, 1_700_000_000.0)

    def test_compile_payload_binaries_pins_the_two_executables(self):
        with tempfile.TemporaryDirectory(prefix="fsv-pin-bins-") as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            built = root / "built"
            built.mkdir()
            for name in ("FSVLauncher.exe", "FlyingSnowVelvetUninstaller.exe"):
                (built / name).write_bytes(name.encode("ascii"))
            with mock.patch.object(
                installer, "_prepare_native_sources", return_value=(root, root, root)
            ), mock.patch.object(
                installer,
                "_compile_payload_binary",
                side_effect=lambda **kwargs: built / kwargs["output_name"],
            ):
                installer.compile_payload_binaries(payload, root, root, root, root)
            for name in ("启动飞行雪绒.exe", "卸载飞行雪绒.exe"):
                with self.subTest(name=name):
                    target = payload / "app" / name
                    self.assertTrue(target.is_file())
                    self.assertEqual(
                        target.stat().st_mtime, installer.PACKAGED_FILE_MTIME
                    )

    def test_online_marker_archive_is_deterministic(self):
        # 在线版 EXE 内置的小归档也必须逐字节可复现：``ZipFile.writestr`` 收到字符串名时
        # 会把当前时间写进条目，这里让时钟每次都不同，两次生成的归档仍必须相同。
        with tempfile.TemporaryDirectory(prefix="fsv-online-marker-") as temporary:
            root = Path(temporary)
            # 先按真实时钟取好一串不同的时刻，再让 ``time.localtime`` 每次返回下一个。
            ticks = iter(
                [time.localtime(moment) for moment in
                 (1_600_000_000, 1_700_000_000, 1_800_000_000) * 8]
            )
            with mock.patch("time.localtime", side_effect=lambda *_: next(ticks)):
                first = root / "first.zip"
                second = root / "second.zip"
                installer.create_online_marker_archive(first)
                installer.create_online_marker_archive(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as bundle:
                self.assertEqual(
                    sorted(bundle.namelist()),
                    sorted([".fsv-online-resource-required", installer.MARKER_NAME]),
                )
                self.assertEqual(bundle.read(installer.MARKER_NAME), installer.MARKER_BYTES)

    def test_sharded_archive_entries_use_the_fixed_timestamp(self):
        # 打包器自己造的条目（分片、索引、占位）都必须显式带上固定时间戳；只要有一条走
        # ``writestr(名字字符串)``，时刻就会跟着构建时钟变，两次打包不再是同一份字节。
        with tempfile.TemporaryDirectory(prefix="fsv-shard-clock-") as temporary:
            root = Path(temporary)
            payload = root / "payload"
            (payload / "app").mkdir(parents=True)
            (payload / "app" / "data.bin").write_bytes(b"shard payload " * 64)
            (payload / installer.MARKER_NAME).write_bytes(installer.MARKER_BYTES)
            installer.pin_packaged_mtime(payload / installer.MARKER_NAME)
            archive = root / "payload.zip"

            installer.create_archive(payload, archive)

            with zipfile.ZipFile(archive) as bundle:
                members = bundle.infolist()
                self.assertGreater(len(members), 2)
                for member in members:
                    if member.filename == installer.MARKER_NAME:
                        # 标记按文件写出，条目时间戳来自它那份固定的 mtime。
                        continue
                    with self.subTest(member=member.filename):
                        self.assertEqual(
                            member.date_time, installer.ARCHIVE_ENTRY_DATE_TIME
                        )
            self.assertEqual(installer.ARCHIVE_ENTRY_DATE_TIME, (1980, 1, 1, 0, 0, 0))

    def test_reproducible_check_keeps_one_copy_and_rejects_drift(self):
        with tempfile.TemporaryDirectory(prefix="fsv-repro-") as temporary:
            root = Path(temporary)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            for directory in (first_dir, second_dir):
                (directory / "installer.exe").write_bytes(b"installer")
                (directory / "resources.zip").write_bytes(b"resources")
            first = (first_dir / "installer.exe", first_dir / "resources.zip")
            second = (second_dir / "installer.exe", second_dir / "resources.zip")
            digests = installer._verify_reproducible(first, second)
            self.assertEqual(
                digests["installer.exe"], installer._sha256_file(first[0])
            )
            self.assertEqual(set(digests), {"installer.exe", "resources.zip"})
            (second_dir / "resources.zip").write_bytes(b"other resources")
            with self.assertRaises(SystemExit):
                installer._verify_reproducible(first, second)
            with self.assertRaises(SystemExit):
                installer._verify_reproducible(first, (second_dir / "installer.exe",))

class ResourceBundleOverlayTests(unittest.TestCase):
    """在线资源包双读：分片归档与旧的 Deflate 归档都要能覆盖安装。"""

    def write_payload(self, root: Path, files: dict[str, bytes]) -> Path:
        payload = root / "payload"
        for name, data in files.items():
            path = payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (payload / installer.MARKER_NAME).write_bytes(installer.MARKER_BYTES)
        return payload

    def test_sharded_resource_archive_overlays_every_file(self):
        files = {
            "app/data.bin": bytes(range(256)) * 900,
            "app/services/dsh/package.json": b'{"name":"dsh"}' * 40,
            "runtime/python311/python.exe": b"stub" * 100,
            "app/empty.dat": b"",
        }
        with tempfile.TemporaryDirectory(prefix="fsv-resource-sharded-") as temporary:
            root = Path(temporary)
            payload = self.write_payload(root, files)
            archive = root / "resources.zip"
            # 收紧分片目标，强制落到多条分片上，覆盖跨分片的偏移与顺序。
            with mock.patch.object(installer, "PAYLOAD_SHARD_TARGET_BYTES", 1000):
                installer.create_resource_archive(payload, archive, sharded=True)
            with zipfile.ZipFile(archive) as bundle:
                shards = [name for name in bundle.namelist() if name.startswith(SHARD_PREFIX)]
            self.assertGreater(len(shards), 1)
            install_root = root / "install"
            install_root.mkdir()

            install_resource_bundle(archive, install_root)

            # 安装标记只在 staging 里校验，覆盖安装时不会被复制过去。
            self.assertFalse((install_root / installer.MARKER_NAME).exists())
            for name, data in files.items():
                with self.subTest(name=name):
                    self.assertEqual((install_root / name).read_bytes(), data)

    def test_deflated_resource_archive_still_overlays(self):
        files = {"app/data.bin": b"resource payload" * 512}
        with tempfile.TemporaryDirectory(prefix="fsv-resource-plain-") as temporary:
            root = Path(temporary)
            payload = self.write_payload(root, files)
            archive = root / "resources.zip"
            installer.create_resource_archive(payload, archive)
            install_root = root / "install"
            install_root.mkdir()

            install_resource_bundle(archive, install_root)

            self.assertEqual(
                (install_root / "app/data.bin").read_bytes(), files["app/data.bin"]
            )

    def test_sharded_resource_archive_rejects_a_missing_shard(self):
        with tempfile.TemporaryDirectory(prefix="fsv-resource-broken-") as temporary:
            root = Path(temporary)
            archive = root / "resources.zip"
            rows = struct.pack("<I", 1) + installer.PAYLOAD_SHARD_INDEX_ROW.pack(0, 0, 4)
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(installer.PAYLOAD_SHARD_INDEX_NAME, rows)
                bundle.writestr("app/a.bin", b"")
                bundle.writestr(installer.MARKER_NAME, installer.MARKER_BYTES)
            install_root = root / "install"
            install_root.mkdir()

            with self.assertRaises(ValueError):
                install_resource_bundle(archive, install_root)
            self.assertFalse((install_root / "app" / "a.bin").exists())

    def test_sharded_resource_archive_rejects_truncated_data(self):
        with tempfile.TemporaryDirectory(prefix="fsv-resource-cut-") as temporary:
            root = Path(temporary)
            archive = root / "resources.zip"
            compressor = lzma.LZMACompressor(
                format=lzma.FORMAT_RAW, filters=list(installer.PAYLOAD_SHARD_FILTERS)
            )
            shard = compressor.compress(b"0123456789") + compressor.flush()
            # 索引声明 20 字节，分片只解得出 10 字节，更新器必须报错收场。
            rows = struct.pack("<I", 1) + installer.PAYLOAD_SHARD_INDEX_ROW.pack(0, 0, 20)
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr(installer.PAYLOAD_SHARD_INDEX_NAME, rows)
                bundle.writestr(
                    installer.PAYLOAD_SHARD_NAME_TEMPLATE.format(index=0), shard
                )
                info = zipfile.ZipInfo("app/a.bin")
                info.compress_type = zipfile.ZIP_STORED
                bundle.writestr(info, b"")
                bundle.writestr(installer.MARKER_NAME, installer.MARKER_BYTES)
            install_root = root / "install"
            install_root.mkdir()

            with self.assertRaises(ValueError):
                install_resource_bundle(archive, install_root)


def _directory_link(logical: Path, physical: Path) -> bool:
    """建一个指向 physical 的目录链接 / junction；建不出来时返回 False。"""
    try:
        logical.symlink_to(physical, target_is_directory=True)
        return True
    except OSError:
        pass
    if os.name != "nt":
        return False
    junction = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(logical), str(physical)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return junction.returncode == 0


class ResourceOverlayLockTests(unittest.TestCase):
    """被扫盘/杀毒占用的文件跳过并重试，其它文件照常替换。"""

    def write_payload(self, root: Path, files: dict[str, bytes]) -> Path:
        payload = root / "payload"
        for name, data in files.items():
            path = payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (payload / installer.MARKER_NAME).write_bytes(installer.MARKER_BYTES)
        return payload

    def write_archive(self, root: Path, files: dict[str, bytes]) -> Path:
        payload = self.write_payload(root, files)
        archive = root / "resources.zip"
        installer.create_resource_archive(payload, archive)
        return archive

    def test_overlay_retries_a_locked_file(self):
        files = {"app/a.bin": b"a" * 32, "app/b.bin": b"b" * 32}
        with tempfile.TemporaryDirectory(prefix="fsv-resource-locked-") as temporary:
            root = Path(temporary)
            archive = self.write_archive(root, files)
            install_root = root / "install"
            install_root.mkdir()
            # 用 resolve() 比较：CI 的 TEMP 走 8.3 短名（RUNNER~1），未解析的
            # 路径与安装根解析后的写法不同，直接比 Path 会漏掉这次注入。
            locked = (install_root / "app" / "b.bin").resolve()
            real_copy2 = shutil.copy2
            blocked: list[Path] = []

            def flaky_copy(source, destination, *args, **kwargs):
                if Path(destination).resolve() == locked and not blocked:
                    blocked.append(locked)
                    raise PermissionError(32, "file in use")
                return real_copy2(source, destination, *args, **kwargs)

            with mock.patch.object(update_installer, "RESOURCE_OVERLAY_RETRY_DELAY", 0.0):
                with mock.patch.object(
                    update_installer.shutil, "copy2", side_effect=flaky_copy
                ):
                    install_resource_bundle(archive, install_root)

            self.assertEqual(blocked, [locked])
            for name, data in files.items():
                with self.subTest(name=name):
                    self.assertEqual((install_root / name).read_bytes(), data)

    def test_overlay_defers_what_stays_locked_and_applies_it_on_the_next_start(self):
        """一直被占用的文件不再让整次更新失败，登记后由下次启动补装。"""
        files = {"app/a.bin": b"a" * 32, "app/b.bin": b"b" * 32}
        with tempfile.TemporaryDirectory(prefix="fsv-resource-stuck-") as temporary:
            root = Path(temporary)
            archive = self.write_archive(root, files)
            install_root = root / "install"
            install_root.mkdir()
            pending = root / "pending"

            def always_locked(*args, **kwargs):
                raise PermissionError(32, "file in use")

            with (
                mock.patch.object(update_installer, "RESOURCE_OVERLAY_RETRY_DELAY", 0.0),
                mock.patch.object(
                    update_installer.shutil, "copy2", side_effect=always_locked
                ),
                mock.patch.object(
                    update_installer, "pending_overlay_root", return_value=pending
                ),
            ):
                outcome = install_resource_bundle(archive, install_root)

            self.assertEqual(outcome.locked, ("app/a.bin", "app/b.bin"))
            self.assertIsNotNone(outcome.staging)
            self.assertEqual(outcome.target_root, install_root.resolve())
            self.assertFalse((install_root / "app" / "a.bin").exists())

            with mock.patch.object(
                update_installer, "pending_overlay_root", return_value=pending
            ):
                deferred = defer_overlay_leftovers(
                    outcome, install_root, release={"tag": "PACK"}
                )
                self.assertEqual(deferred, ("app/a.bin", "app/b.bin"))
                self.assertFalse(Path(outcome.staging).exists())
                self.assertTrue((pending / "manifest.json").is_file())

                applied = apply_pending_overlay(install_root)
                self.assertEqual(applied, ("app/a.bin", "app/b.bin"))
                # 补装完成后目录与清单一起清掉，不会留在磁盘上。
                self.assertFalse(pending.exists())

            for name, data in files.items():
                with self.subTest(name=name):
                    self.assertEqual((install_root / name).read_bytes(), data)

    def test_pending_overlay_recognizes_a_differently_spelled_install_root(self):
        """登记与补装之间安装根换了种写法，也要认得出来。

        登记时写的是调用方给的安装根，补装时比的是 ``resolve()`` 之后的安装根：CI 上两者
        一个带短名、一个是真实路径，按字面比较会把待补装项当成别的安装目录丢掉，被占用的
        文件于是永远补不上。
        """
        with tempfile.TemporaryDirectory(prefix="fsv-resource-alias-") as temporary:
            base = Path(temporary)
            real = base / "install"
            (real / "app").mkdir(parents=True)
            (real / "app" / "a.bin").write_bytes(b"old")
            link = base / "link"
            if not _directory_link(link, real):
                self.skipTest("无法创建目录链接，跳过写法归一验证")
            pending = base / "pending"
            staging = base / "staging"
            (staging / "app").mkdir(parents=True)
            (staging / "app" / "a.bin").write_bytes(b"new")
            outcome = update_installer.OverlayOutcome(
                locked=("app/a.bin",), staging=staging, target_root=real
            )

            with mock.patch.object(
                update_installer, "pending_overlay_root", return_value=pending
            ):
                deferred = defer_overlay_leftovers(outcome, link, release={"tag": "PACK"})
                self.assertEqual(deferred, ("app/a.bin",))
                applied = apply_pending_overlay(link)

            self.assertEqual(applied, ("app/a.bin",))
            self.assertEqual((real / "app" / "a.bin").read_bytes(), b"new")

    def test_pending_overlay_skips_files_replaced_by_another_flow(self):
        """登记后又被动过的目标文件不能被旧字节覆盖。"""
        with tempfile.TemporaryDirectory(prefix="fsv-resource-stale-") as temporary:
            root = Path(temporary)
            install_root = root / "install"
            install_root.mkdir()
            target_file = install_root / "app" / "a.bin"
            target_file.parent.mkdir(parents=True)
            target_file.write_bytes(b"old")
            pending = root / "pending"
            staged = pending / update_installer.PENDING_OVERLAY_FILES_NAME / "app" / "a.bin"
            staged.parent.mkdir(parents=True)
            staged.write_bytes(b"new")
            outcome = update_installer.OverlayOutcome(
                locked=("app/a.bin",), staging=root / "staging", target_root=install_root
            )
            (root / "staging" / "app").mkdir(parents=True)
            (root / "staging" / "app" / "a.bin").write_bytes(b"new")

            with mock.patch.object(
                update_installer, "pending_overlay_root", return_value=pending
            ):
                defer_overlay_leftovers(outcome, install_root)
                # 完整安装器后来换过这个文件：大小与 mtime 都变了。
                target_file.write_bytes(b"replaced by the offline installer")
                applied = apply_pending_overlay(install_root)
                # 不适用的补装项连同待补装目录一起丢掉，不留下残留。
                self.assertFalse(pending.exists())

            self.assertEqual(applied, ())
            self.assertEqual(target_file.read_bytes(), b"replaced by the offline installer")

    def test_overlay_skips_files_that_are_already_identical(self):
        """内容一致的文件不重写：省掉一次全量写入，也不去撞别人的文件句柄。"""
        files = {"app/a.bin": b"a" * 4096, "app/b.bin": b"changed"}
        with tempfile.TemporaryDirectory(prefix="fsv-resource-same-") as temporary:
            root = Path(temporary)
            archive = self.write_archive(root, files)
            install_root = root / "install"
            (install_root / "app").mkdir(parents=True)
            (install_root / "app" / "a.bin").write_bytes(files["app/a.bin"])
            (install_root / "app" / "b.bin").write_bytes(b"stale")

            real_copy2 = shutil.copy2
            copied: list[str] = []

            def tracking_copy(source, destination, *args, **kwargs):
                copied.append(Path(destination).name)
                return real_copy2(source, destination, *args, **kwargs)

            with mock.patch.object(
                update_installer.shutil, "copy2", side_effect=tracking_copy
            ):
                outcome = install_resource_bundle(archive, install_root)

            self.assertEqual(outcome.locked, ())
            self.assertEqual(copied, ["b.bin"])
            self.assertEqual((install_root / "app" / "a.bin").read_bytes(), files["app/a.bin"])
            self.assertEqual((install_root / "app" / "b.bin").read_bytes(), b"changed")


if __name__ == "__main__":
    unittest.main()
