import tempfile
import unittest
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import install_deps

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstallDepsResourceTests(unittest.TestCase):
    def test_load_resource_links_indexes_urls_by_filename(self):
        links = install_deps.load_resource_links(PROJECT_ROOT / "resc.net.txt")

        self.assertIn("vosk-model-small-cn-0.22.zip", links)
        self.assertNotIn("chrome-runtime.zip", links)
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
            destination = Path(temp_dir) / "resource.bin"

            def fake_download(url, dest_path, **_kwargs):
                self.assertEqual(url, "https://example.test/resource.zip")
                dest_path.write_bytes(b"resource-data")

            with patch.object(
                install_deps, "_resource_urls", return_value=("https://example.test/resource.zip",)
            ), patch.object(install_deps, "_stream_download_with_progress", side_effect=fake_download):
                result = install_deps._download_resource_file(
                    "resource.bin",
                    destination,
                    label="test resource",
                )

            self.assertTrue(result)
            self.assertEqual(destination.read_bytes(), b"resource-data")

    def test_download_resource_file_rejects_bad_zip_and_uses_next_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "resource.zip"
            archive_buffer = io.BytesIO()
            with zipfile.ZipFile(archive_buffer, "w") as archive:
                archive.writestr("payload.txt", "ok")
            downloads = []

            def fake_download(url, dest_path, **_kwargs):
                downloads.append(url)
                dest_path.write_bytes(b"not-a-zip" if len(downloads) == 1 else archive_buffer.getvalue())

            with patch.object(
                install_deps,
                "_resource_urls",
                return_value=("https://first.invalid/resource.zip", "https://second.invalid/resource.zip"),
            ), patch.object(install_deps, "_stream_download_with_progress", side_effect=fake_download):
                result = install_deps._download_resource_file(
                    "resource.zip", destination, label="test resource"
                )

            self.assertTrue(result)
            self.assertEqual(downloads, ["https://first.invalid/resource.zip", "https://second.invalid/resource.zip"])
            with zipfile.ZipFile(destination, "r") as archive:
                self.assertEqual(archive.read("payload.txt"), b"ok")

    def test_stream_download_resumes_an_existing_part_file(self):
        class FakeResponse(io.BytesIO):
            status = 206
            headers = {"Content-Length": "3"}

            def getcode(self):
                return self.status

        class FakeOpener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return FakeResponse(b"def")

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "resource.zip.part"
            destination.write_bytes(b"abc")
            opener = FakeOpener()
            with patch.object(install_deps.urllib.request, "build_opener", return_value=opener):
                install_deps._stream_download_with_progress(
                    "https://example.invalid/resource.zip",
                    destination,
                    label="test",
                )

            self.assertEqual(destination.read_bytes(), b"abcdef")
            self.assertEqual(opener.request.get_header("Range"), "bytes=3-")

if __name__ == "__main__":
    unittest.main()
