import importlib.util
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "install_deps.py"
SPEC = importlib.util.spec_from_file_location("install_deps_under_test", MODULE_PATH)
install_deps = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(install_deps)


class InstallDepsPlaywrightRuntimeTests(unittest.TestCase):
    def test_load_resource_links_indexes_urls_by_filename(self):
        links = install_deps.load_resource_links(PROJECT_ROOT / "resc.net.txt")

        self.assertIn("vosk-model-small-cn-0.22.zip", links)
        self.assertIn("chrome-runtime.z01", links)
        self.assertEqual(len(links["SEanima.zip"]), 2)
        self.assertTrue(all(url.endswith("/SEanima.zip") for url in links["SEanima.zip"]))
        self.assertEqual(
            {install_deps.urllib.parse.urlsplit(url).hostname for url in links["SEanima.zip"]},
            {"gitee.com", "github.com"},
        )
        self.assertEqual(
            links[install_deps.JIEBA_FAST_WHEEL_NAME],
            (
                "https://gitee.com/Mark42IRPC/Aemeath-AIdeskpet/releases/download/RESC/"
                + install_deps.JIEBA_FAST_WHEEL_NAME,
                "https://github.com/MARK42IRPC/FlyingSnowVelvet-Aemeath/releases/download/RESC/"
                + install_deps.JIEBA_FAST_WHEEL_NAME,
            ),
        )

    def test_ping_host_average_uses_three_attempts_and_timeout_penalty(self):
        with patch.object(install_deps, "_ping_once_ms", side_effect=[10.0, None, 20.0]) as ping_mock:
            latency = install_deps._ping_host_average_ms("example.test")

        self.assertAlmostEqual(latency, (10.0 + 5000.0 + 20.0) / 3.0)
        self.assertEqual(ping_mock.call_count, 3)
        self.assertTrue(all(call.kwargs["timeout"] == 5.0 for call in ping_mock.call_args_list))

    def test_resource_urls_follow_benchmarked_source_order(self):
        urls = (
            "https://gitee.com/example/releases/download/RESC/resource.zip",
            "https://github.com/example/releases/download/RESC/resource.zip",
        )
        with patch.object(
            install_deps,
            "_benchmark_resource_sources",
            return_value=("github.com", "gitee.com"),
        ):
            ordered = install_deps._order_resource_urls(urls)

        self.assertEqual(install_deps.urllib.parse.urlsplit(ordered[0]).hostname, "github.com")
        self.assertEqual(install_deps.urllib.parse.urlsplit(ordered[1]).hostname, "gitee.com")

    def test_download_resource_file_uses_manifest_url_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "resource.zip"

            def fake_download(url, dest_path, **_kwargs):
                self.assertEqual(url, "https://example.test/resource.zip")
                dest_path.write_bytes(b"resource-data")

            with patch.object(
                install_deps, "_resource_urls", return_value=("https://example.test/resource.zip",)
            ), patch.object(install_deps, "_stream_download_with_progress", side_effect=fake_download):
                result = install_deps._download_resource_file(
                    "resource.zip",
                    destination,
                    label="test resource",
                )

            self.assertTrue(result)
            self.assertEqual(destination.read_bytes(), b"resource-data")

    def test_browser_runtime_downloads_show_resource_sequence(self):
        with patch.object(install_deps, "_download_resource_file", return_value=True) as download_mock:
            result = install_deps._ensure_browser_runtime_archives()

        self.assertTrue(result)
        self.assertEqual(
            [call.kwargs["display_sequence"] for call in download_mock.call_args_list],
            [(1, 3), (2, 3), (3, 3)],
        )

    def test_merge_split_zip_creates_zipfile_compatible_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "original.zip"
            with zipfile.ZipFile(original, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("chrome-win64/chrome.exe", b"browser-runtime")

            payload = bytearray(original.read_bytes())
            eocd_offset = payload.rfind(b"PK\x05\x06")
            self.assertGreaterEqual(eocd_offset, 0)
            total_entries = struct.unpack_from("<H", payload, eocd_offset + 10)[0]
            central_offset = struct.unpack_from("<I", payload, eocd_offset + 16)[0]
            final_volume = bytearray(payload[central_offset:])
            final_eocd = eocd_offset - central_offset
            struct.pack_into("<H", final_volume, final_eocd + 4, 2)
            struct.pack_into("<H", final_volume, final_eocd + 6, 2)
            struct.pack_into("<H", final_volume, final_eocd + 8, total_entries)
            struct.pack_into("<I", final_volume, final_eocd + 16, 0)

            parts = (root / "runtime.z01", root / "runtime.z02", root / "runtime.zip")
            parts[0].write_bytes(payload[:central_offset])
            parts[1].write_bytes(b"")
            parts[2].write_bytes(final_volume)
            merged = root / "merged.zip"

            install_deps._merge_split_zip(parts, merged)

            with zipfile.ZipFile(merged) as archive:
                self.assertEqual(archive.read("chrome-win64/chrome.exe"), b"browser-runtime")

    def test_ensure_browser_runtime_skips_install_when_runtime_exists(self):
        fake_runtime = PROJECT_ROOT / "resc" / "playwright" / "browsers" / "ms-playwright" / "chromium-1208" / "chrome-win64" / "chrome.exe"
        with patch.object(install_deps, "_find_playwright_browser_runtime", return_value=fake_runtime):
            result = install_deps.ensure_yuanbao_browser_runtime("py")

        self.assertTrue(result)

    def test_browser_runtime_completeness_rejects_partial_chromium(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "chrome-win64" / "chrome.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"browser")

            self.assertFalse(install_deps._is_playwright_browser_runtime_complete(executable))

            executable.with_name("chrome.dll").write_bytes(b"dll")
            executable.with_name("icudtl.dat").write_bytes(b"data")
            self.assertTrue(install_deps._is_playwright_browser_runtime_complete(executable))

    def test_find_browser_runtime_skips_partial_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            partial = root / "chromium-1209" / "chrome-win64" / "chrome.exe"
            complete = root / "chromium-1208" / "chrome-win64" / "chrome.exe"
            partial.parent.mkdir(parents=True)
            complete.parent.mkdir(parents=True)
            partial.write_bytes(b"partial")
            complete.write_bytes(b"browser")
            complete.with_name("chrome.dll").write_bytes(b"dll")
            complete.with_name("icudtl.dat").write_bytes(b"data")

            with patch.object(
                install_deps,
                "_candidate_playwright_browser_executables",
                return_value=[partial, complete],
            ):
                result = install_deps._find_playwright_browser_runtime()

        self.assertEqual(result, complete)

    def test_ensure_browser_runtime_fails_without_local_archive(self):
        with patch.object(install_deps, "_find_playwright_browser_runtime", return_value=None), patch.object(
            install_deps, "_ensure_browser_runtime_archives", return_value=False
        ):
            result = install_deps.ensure_yuanbao_browser_runtime("py")

        self.assertFalse(result)

    def test_ensure_browser_runtime_extracts_local_archive_into_repo_runtime_dir(self):
        fake_runtime = PROJECT_ROOT / "resc" / "playwright" / "browsers" / "ms-playwright" / "chromium-1208" / "chrome-win64" / "chrome.exe"
        temp_root = Path(os.environ.get("TEMP", "C:\\Temp")) / "fsv_playwright_runtime"

        with patch.object(
            install_deps,
            "_find_playwright_browser_runtime",
            side_effect=[None, fake_runtime],
        ), patch.object(
            install_deps, "_ensure_browser_runtime_archives", return_value=True
        ), patch.object(install_deps, "_extract_browser_runtime_archive") as extract_mock, patch.object(
            install_deps, "_find_extracted_browser_root", return_value=temp_root / "extract" / "chrome-win64"
        ), patch.object(install_deps.shutil, "move") as move_mock, patch.object(
            install_deps, "_rmtree_if_exists"
        ):
            result = install_deps.ensure_yuanbao_browser_runtime("C:\\Python311\\python.exe")

        self.assertTrue(result)
        extract_mock.assert_called_once_with(temp_root / "extract")
        move_mock.assert_called_once_with(
            str(temp_root / "extract" / "chrome-win64"),
            str(install_deps.PLAYWRIGHT_RUNTIME_TARGET_DIR / "chrome-win64"),
        )


if __name__ == "__main__":
    unittest.main()
