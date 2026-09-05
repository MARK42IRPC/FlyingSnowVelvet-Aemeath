import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile
import zlib

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
        shutil.copy2(source_root / "zip_extract.c", cls.compile_root / "zip_extract.c")
        shutil.copy2(source_root / "zip_extract.h", cls.compile_root / "zip_extract.h")
        shutil.copy2(
            Path(__file__).parent / "native" / "zip_extract_harness.c",
            cls.compile_root / "zip_extract_harness.c",
        )
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
                    '/Fe:"zip_extract_harness.exe"',
                    '"zip_extract_harness.c"',
                    '"zip_extract.c"',
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
