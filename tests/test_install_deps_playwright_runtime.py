import tempfile
import unittest
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

if __name__ == "__main__":
    unittest.main()
