import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile
import zlib
from unittest import mock

from scripts import build_offline_installer as installer


@unittest.skipUnless(os.name == "nt", "native installer extraction is Windows-only")
class WindowsZipExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            vsdevcmd = installer.find_vsdevcmd(None)
        except SystemExit as exc:
            raise unittest.SkipTest(str(exc)) from exc

        cls._temporary = tempfile.TemporaryDirectory(prefix="fsv-native-zip-")
        cls.compile_root = Path(cls._temporary.name)
        source_root = installer.DEFAULT_INSTALLER_SOURCE / "src"
        zlib_root = installer.DEFAULT_INSTALLER_SOURCE / "third_party" / "zlib-1.3.1"
        lzma_root = installer.DEFAULT_INSTALLER_SOURCE / installer.LZMA_DIRECTORY
        shutil.copy2(source_root / "zip_extract.c", cls.compile_root / "zip_extract.c")
        shutil.copy2(source_root / "zip_extract.h", cls.compile_root / "zip_extract.h")
        shutil.copy2(
            Path(__file__).parent / "native" / "zip_extract_harness.c",
            cls.compile_root / "zip_extract_harness.c",
        )
        lzma_compile_root = cls.compile_root / "lzma"
        lzma_compile_root.mkdir(parents=True, exist_ok=True)
        for name in (*installer.LZMA_HEADERS, *installer.LZMA_SOURCES):
            shutil.copy2(lzma_root / name, lzma_compile_root / name)
        installer._compile_zlib(zlib_root, vsdevcmd, cls.compile_root)
        installer.run_vs_command(
            vsdevcmd,
            " ".join(
                [
                    "cl.exe",
                    "/nologo",
                    "/MT",
                    "/O2",
                    "/W4",
                    "/WX",
                    "/utf-8",
                    "/DZ_SOLO",
                    '/I"zlib"',
                    '/I"lzma"',
                    '/Fe:"zip_extract_harness.exe"',
                    '"zip_extract_harness.c"',
                    '"zip_extract.c"',
                    '"lzma\\LzmaDec.c"',
                    '"lzma\\Lzma2Dec.c"',
                    '"zlibstatic.lib"',
                    "/link",
                    "/SUBSYSTEM:CONSOLE",
                ]
            ),
            cls.compile_root,
        )
        cls.harness = cls.compile_root / "zip_extract_harness.exe"

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def run_harness(self, *arguments: Path | str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.harness), *(str(argument) for argument in arguments)],
            check=False,
            capture_output=True,
            text=True,
            encoding="ascii",
            errors="replace",
            timeout=60,
        )

    @staticmethod
    def write_minimal_zip64(path: Path) -> bytes:
        name = b"zip64/payload.txt"
        content = b"zip64 payload"
        checksum = zlib.crc32(content) & 0xFFFFFFFF
        flags = 0x0800
        local_extra = struct.pack("<HHQQ", 0x0001, 16, len(content), len(content))
        local_header = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            45,
            flags,
            0,
            0,
            0,
            checksum,
            0xFFFFFFFF,
            0xFFFFFFFF,
            len(name),
            len(local_extra),
        )
        local_record = local_header + name + local_extra + content
        central_offset = len(local_record)
        central_extra = struct.pack("<HHQQQ", 0x0001, 24, len(content), len(content), 0)
        central_header = struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            45,
            45,
            flags,
            0,
            0,
            0,
            checksum,
            0xFFFFFFFF,
            0xFFFFFFFF,
            len(name),
            len(central_extra),
            0,
            0,
            0,
            0,
            0xFFFFFFFF,
        )
        central_record = central_header + name + central_extra
        zip64_offset = central_offset + len(central_record)
        zip64_eocd = struct.pack(
            "<IQHHIIQQQQ",
            0x06064B50,
            44,
            45,
            45,
            0,
            0,
            1,
            1,
            len(central_record),
            central_offset,
        )
        locator = struct.pack("<IIQI", 0x07064B50, 0, zip64_offset, 1)
        eocd = struct.pack(
            "<IHHHHIIH",
            0x06054B50,
            0,
            0,
            0xFFFF,
            0xFFFF,
            0xFFFFFFFF,
            0xFFFFFFFF,
            0,
        )
        path.write_bytes(local_record + central_record + zip64_eocd + locator + eocd)
        return content

    def test_extracts_unicode_stored_deflated_and_empty_files(self):
        with tempfile.TemporaryDirectory(prefix="fsv-zip-content-") as temporary:
            root = Path(temporary)
            archive = root / "payload.zip"
            destination = root / "中文安装目录"
            with zipfile.ZipFile(archive, "w", allowZip64=True) as output:
                output.writestr("app/动画/stored.bin", b"stored", compress_type=zipfile.ZIP_STORED)
                output.writestr(
                    "runtime/deflated.txt",
                    b"deflated payload" * 4096,
                    compress_type=zipfile.ZIP_DEFLATED,
                )
                output.writestr("app/empty.dat", b"", compress_type=zipfile.ZIP_DEFLATED)

            result = self.run_harness("extract", archive, destination)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((destination / "app" / "动画" / "stored.bin").read_bytes(), b"stored")
            self.assertEqual(
                (destination / "runtime" / "deflated.txt").read_bytes(),
                b"deflated payload" * 4096,
            )
            self.assertEqual((destination / "app" / "empty.dat").read_bytes(), b"")
            self.assertRegex(result.stdout, r"^OK \d+ 3 100\s*$")

    def test_progress_messages_are_coalesced_for_many_small_files(self):
        with tempfile.TemporaryDirectory(prefix="fsv-zip-progress-") as temporary:
            root = Path(temporary)
            archive = root / "many.zip"
            destination = root / "destination"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for index in range(500):
                    output.writestr(f"small/{index:04d}.txt", b"x")

            result = self.run_harness("extract", archive, destination)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            parts = result.stdout.split()
            self.assertEqual(parts[0], "OK")
            self.assertLess(int(parts[1]), 100)
            self.assertEqual(parts[2:], ["500", "100"])

    def test_parallel_extraction_keeps_every_entry_intact(self):
        with tempfile.TemporaryDirectory(prefix="fsv-zip-parallel-") as temporary:
            root = Path(temporary)
            archive = root / "payload.zip"
            destination = root / "destination"
            expected = {}
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                # A wide tree exercises the shared work cursor, the nested
                # folders race on directory creation, and the large member has
                # to survive being unpacked next to many small ones.
                for index in range(240):
                    name = f"app/模块{index % 7}/子目录{index % 5}/file-{index:04d}.txt"
                    data = (f"payload {index} " * (index % 23 + 1)).encode("utf-8")
                    output.writestr(name, data)
                    expected[name] = data
                large = bytes(range(256)) * 32768
                output.writestr("runtime/large.bin", large)
                expected["runtime/large.bin"] = large

            result = self.run_harness("extract", archive, destination)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name, data in expected.items():
                with self.subTest(name=name):
                    self.assertEqual((destination / Path(name)).read_bytes(), data)
            parts = result.stdout.split()
            self.assertEqual(parts[0], "OK")
            self.assertEqual(parts[2:], [str(len(expected)), "100"])

    @staticmethod
    def write_sharded_payload(root: Path) -> tuple[Path, dict[str, bytes]]:
        payload = root / "payload"
        expected = {
            # Large enough to cross the decoder's input and output buffers.
            "app/动画/大文件.bin": bytes(range(256)) * 20000,
            "app/模块/小文件.txt": b"shard payload " * 7,
            "runtime/empty.dat": b"",
            ".fsv-install-root": installer.MARKER_BYTES,
        }
        for name, data in expected.items():
            path = payload / Path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        archive = root / "payload.zip"
        installer.create_archive(payload, archive)
        return archive, expected

    def test_sharded_archive_round_trips_every_placeholder(self):
        with tempfile.TemporaryDirectory(prefix="fsv-zip-shard-") as temporary:
            root = Path(temporary)
            archive, expected = self.write_sharded_payload(root)
            destination = root / "分片安装目录"
            with zipfile.ZipFile(archive) as bundle:
                self.assertIsNone(bundle.testzip())
                names = [member.filename for member in bundle.infolist()]
                self.assertIn(installer.PAYLOAD_SHARD_INDEX_NAME, names)
                self.assertTrue(
                    [
                        name
                        for name in names
                        if name.startswith(".fsv-shard-") and name.endswith(".fsvlzma")
                    ]
                )
                placeholders = [
                    member
                    for member in bundle.infolist()
                    if member.compress_size == 0
                    and not member.filename.startswith(".fsv-shard-")
                ]
                # The compatibility view still lists every payload path, with
                # the real size, but no bytes of its own.
                self.assertEqual(
                    {member.filename for member in placeholders},
                    set(expected) - {".fsv-install-root"},
                )
                for member in placeholders:
                    self.assertEqual(member.file_size, len(expected[member.filename]))

            result = self.run_harness("extract", archive, destination)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name, data in expected.items():
                with self.subTest(name=name):
                    self.assertEqual((destination / Path(name)).read_bytes(), data)
            self.assertEqual(result.stdout.split()[2:], [str(len(expected)), "100"])
            stats = self.run_harness("stats", archive)
            self.assertEqual(stats.stdout.split()[:1], ["OK"])
            self.assertEqual(
                stats.stdout.split()[1:],
                [str(len(expected)), str(sum(len(data) for data in expected.values()))],
            )

    def test_sharded_archive_rejects_a_damaged_shard(self):
        with tempfile.TemporaryDirectory(prefix="fsv-zip-shard-crc-") as temporary:
            root = Path(temporary)
            archive, _ = self.write_sharded_payload(root)
            with zipfile.ZipFile(archive) as bundle:
                shard = next(
                    member
                    for member in bundle.infolist()
                    if member.filename.startswith(".fsv-shard-")
                    and member.filename.endswith(".fsvlzma")
                )
            raw = bytearray(archive.read_bytes())
            name_length, extra_length = struct.unpack_from("<HH", raw, shard.header_offset + 26)
            data_offset = shard.header_offset + 30 + name_length + extra_length
            raw[data_offset + 4096 % shard.compress_size] ^= 0xFF
            archive.write_bytes(bytes(raw))

            result = self.run_harness("extract", archive, root / "destination")

            self.assertNotEqual(result.returncode, 0)

    def test_sharded_archive_rejects_a_mismatched_index(self):
        with tempfile.TemporaryDirectory(prefix="fsv-zip-shard-index-") as temporary:
            root = Path(temporary)
            archive, _ = self.write_sharded_payload(root)
            with zipfile.ZipFile(archive) as bundle:
                member = bundle.getinfo(installer.PAYLOAD_SHARD_INDEX_NAME)
            raw = bytearray(archive.read_bytes())
            name_length, extra_length = struct.unpack_from("<HH", raw, member.header_offset + 26)
            data_offset = member.header_offset + 30 + name_length + extra_length
            # Claim a size that the placeholder entry contradicts.
            raw[data_offset + 4 + 16] ^= 0x01
            archive.write_bytes(bytes(raw))

            result = self.run_harness("extract", archive, root / "destination")

            self.assertNotEqual(result.returncode, 0)

    def test_sharded_archive_splits_across_several_shards(self):
        with tempfile.TemporaryDirectory(prefix="fsv-zip-shard-multi-") as temporary:
            root = Path(temporary)
            with mock.patch.object(installer, "PAYLOAD_SHARD_TARGET_BYTES", 4096):
                archive, expected = self.write_sharded_payload(root)
            with zipfile.ZipFile(archive) as bundle:
                self.assertGreater(
                    len(
                        [
                            member
                            for member in bundle.infolist()
                            if member.filename.endswith(".fsvlzma")
                        ]
                    ),
                    1,
                )
                self.assertIsNone(bundle.testzip())

            destination = root / "destination"
            result = self.run_harness("extract", archive, destination)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name, data in expected.items():
                with self.subTest(name=name):
                    self.assertEqual((destination / Path(name)).read_bytes(), data)

    def test_worker_pool_and_peak_gate_leave_headroom(self):
        # 12 logical processors used to open eight inflate threads and peg every
        # core even though two to eight workers measure the same throughput.
        sizing = {1: 1, 2: 1, 4: 2, 6: 4, 8: 4, 12: 4, 64: 4}
        for logical, expected in sizing.items():
            with self.subTest(logical=logical):
                result = self.run_harness("tune", str(logical), "17300", "0", "0")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                fields = result.stdout.split()
                self.assertEqual(fields[0], "OK")
                self.assertEqual(fields[1], str(expected))
                # An idle machine still never exceeds the whole-machine
                # ceiling or half of the logical processors.
                idle_limit = min(
                    expected,
                    max(1, 65 * logical // 100),
                    max(1, logical // 2),
                )
                self.assertEqual(fields[2], str(idle_limit))
        # Fewer entries than workers must not spawn idle threads.
        result = self.run_harness("tune", "12", "3", "0", "0")
        self.assertEqual(result.stdout.split(), ["OK", "3", "3"])
        # The active limit follows the measured headroom: an idle machine runs
        # the whole pool, and a machine that is already busy drops towards one
        # worker instead of adding to the stall.
        cases = (
            (12, "17300", 0, 0, 4),
            (12, "17300", 60, 40, 4),
            (12, "17300", 40, 0, 3),
            (12, "17300", 55, 0, 1),
            (12, "17300", 80, 10, 1),
            (12, "17300", 100, 40, 1),
            (4, "17300", 0, 0, 2),
            (8, "17300", 30, 5, 3),
        )
        for logical, entries, busy, ours, expected in cases:
            with self.subTest(logical=logical, busy=busy, ours=ours):
                result = self.run_harness(
                    "tune", str(logical), entries, str(busy), str(ours)
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.split()[2], str(expected))

    def test_remaining_time_uses_the_average_entry_rate_of_one_window(self):
        # Remaining time is the entries finished inside the last two second
        # window divided into the entries still outstanding, so the estimate is
        # republished at most once per window instead of on every progress post.
        cases = (
            # total, completed, finished, window_ms, seconds
            (17300, 1300, 80, 2000, 400),   # 40 entries/s -> 16000 / 40
            (1000, 500, 20, 2000, 50),      # 10 entries/s -> 500 / 10
            (1000, 0, 10, 2000, 200),       # 5 entries/s -> 1000 / 5
            (1000, 900, 1, 2000, 200),      # 0.5 entries/s -> 100 / 0.5
            (1000, 999, 1, 2000, 2),        # sub-second work never rounds to 0
            (1000, 999, 1, 1000, 1),        # 1 entry/s -> 1 / 1
            (1000, 1000, 5, 2000, 0),       # nothing left, keep the last value
            (1000, 500, 0, 2000, 0),        # a window that finished nothing
            (1000, 500, 5, 0, 0),           # a window without duration
            (0, 0, 5, 2000, 0),             # the entry count is still unknown
        )
        for total, completed, finished, window, expected in cases:
            with self.subTest(total=total, completed=completed, finished=finished, window=window):
                result = self.run_harness(
                    "eta", str(total), str(completed), str(finished), str(window)
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.split(), ["OK", str(expected)])

    def test_zip64_entry_count_is_supported(self):
        with tempfile.TemporaryDirectory(prefix="fsv-zip64-") as temporary:
            root = Path(temporary)
            archive = root / "zip64.zip"
            destination = root / "destination"
            content = self.write_minimal_zip64(archive)

            result = self.run_harness("stats", archive)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.split(), ["OK", "1", str(len(content))])
            extraction = self.run_harness("extract", archive, destination)
            self.assertEqual(extraction.returncode, 0, extraction.stdout + extraction.stderr)
            self.assertEqual((destination / "zip64" / "payload.txt").read_bytes(), content)

    def test_rejects_paths_that_windows_can_normalize_outside_the_destination(self):
        unsafe_names = (
            "../escape.txt",
            "folder/../../escape.txt",
            ".. /escape.txt",
            "folder./../escape.txt",
            "/absolute.txt",
            "C:/drive.txt",
            "CON/device.txt",
        )
        with tempfile.TemporaryDirectory(prefix="fsv-zip-traversal-") as temporary:
            root = Path(temporary)
            for index, unsafe_name in enumerate(unsafe_names):
                with self.subTest(name=unsafe_name):
                    archive = root / f"unsafe-{index}.zip"
                    destination = root / f"destination-{index}"
                    with zipfile.ZipFile(archive, "w") as output:
                        output.writestr(unsafe_name, b"must not escape")

                    result = self.run_harness("extract", archive, destination)

                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse((root / "escape.txt").exists())
                    self.assertFalse((root / "absolute.txt").exists())
                    self.assertFalse((root / "drive.txt").exists())


if __name__ == "__main__":
    unittest.main()
