"""Contract tests for the sharded LZMA2 payload archive.

The native extractor is exercised by ``tests/test_windows_zip_extract.py``;
these checks pin the on-disk format itself, the constants the C decoder has to
agree with, and the parts of the contract the in-app updater depends on.
"""

from __future__ import annotations

import lzma
import struct
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import build_offline_installer as installer
from lib.script.app.update_installer import install_resource_bundle


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


if __name__ == "__main__":
    unittest.main()
