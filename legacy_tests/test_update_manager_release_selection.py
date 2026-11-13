from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from lib.script.update_manager import (
    InstalledState,
    ReleaseInfo,
    UpdateError,
    UpdateManager,
    _parse_voice_package_release,
    _select_release_source,
    _is_retryable_request_error,
)


class UpdateManagerReleaseSelectionTests(unittest.TestCase):
    def test_only_transient_http_errors_are_retried(self):
        response = Mock(status_code=405)
        self.assertFalse(_is_retryable_request_error(requests.HTTPError(response=response)))
        response.status_code = 503
        self.assertTrue(_is_retryable_request_error(requests.HTTPError(response=response)))
        self.assertTrue(_is_retryable_request_error(requests.ConnectionError("reset")))

    def test_newer_release_wins_before_network_latency(self):
        older_fast = ReleaseInfo(
            "LTS1.0.7pre2",
            datetime(2026, 7, 28, tzinfo=timezone.utc),
            "huggingface.zip",
            "huggingface",
            "Hugging Face",
            "hf:1",
            0.1,
        )
        newer_slow = ReleaseInfo(
            "LTS1.0.7pre3",
            datetime(2026, 7, 29, tzinfo=timezone.utc),
            "modelscope.zip",
            "modelscope",
            "ModelScope",
            "ms:1",
            2.0,
        )
        self.assertIs(_select_release_source([older_fast, newer_slow]), newer_slow)

    def test_equal_release_time_uses_faster_source(self):
        published = datetime(2026, 7, 29, tzinfo=timezone.utc)
        slow = ReleaseInfo("LTS2", published, "a.zip", "a", "Hugging Face", "a", 1.5)
        fast = ReleaseInfo("LTS2", published, "b.zip", "b", "ModelScope", "b", 0.2)
        self.assertIs(_select_release_source([slow, fast]), fast)

    def test_one_failed_source_does_not_block_the_other(self):
        available = ReleaseInfo(
            "LTS2",
            datetime(2026, 7, 29, tzinfo=timezone.utc),
            "FlyingSnowVelvet-LTS2-Offline-Installer.zip",
            "download",
            "Hugging Face",
            "revision",
            0.1,
            (),
            "a" * 64,
        )
        manager = UpdateManager()
        with (
            patch.object(manager, "_fetch_huggingface_voice_release", return_value=available),
            patch.object(
                manager,
                "_fetch_modelscope_voice_release",
                side_effect=requests.ConnectionError("offline"),
            ),
        ):
            self.assertEqual(manager._fetch_latest_release(), available)

    def test_same_revision_source_is_attached_as_download_fallback(self):
        published = datetime(2026, 7, 29, tzinfo=timezone.utc)
        huggingface = ReleaseInfo(
            "LTS2", published, "hf.zip", "huggingface", "Hugging Face", "same", 0.2, (), "a" * 64
        )
        modelscope = ReleaseInfo(
            "LTS2", published, "ms.zip", "modelscope", "ModelScope", "same", 0.1, (), "a" * 64
        )
        manager = UpdateManager()
        with (
            patch.object(manager, "_fetch_huggingface_voice_release", return_value=huggingface),
            patch.object(manager, "_fetch_modelscope_voice_release", return_value=modelscope),
        ):
            selected = manager._fetch_latest_release()

        self.assertEqual(selected.source, "ModelScope")
        self.assertEqual(selected.fallback_download_urls, ("huggingface",))

    def test_voice_package_manifest_builds_versioned_zip_release(self):
        digest = "a" * 64
        release = _parse_voice_package_release(
            {
                "format": "fsv-offline-installer-v1",
                "version": "LTS2",
                "published_at": "2026-07-29T00:00:00Z",
                "revision": "same",
                "asset_name": "FlyingSnowVelvet-LTS2-Offline-Installer.zip",
                "asset_path": "updates/FlyingSnowVelvet-LTS2-Offline-Installer.zip",
                "sha256": digest,
            },
            source_name="Hugging Face",
            file_base_url="https://example.test/repo/resolve/main/",
            response_seconds=0.1,
        )
        self.assertEqual(release.tag, "LTS2")
        self.assertEqual(release.archive_sha256, digest)
        self.assertTrue(release.download_url.endswith("updates/FlyingSnowVelvet-LTS2-Offline-Installer.zip"))

    def test_download_switches_to_same_revision_fallback(self):
        published = datetime(2026, 7, 29, tzinfo=timezone.utc)
        release = ReleaseInfo(
            "LTS2",
            published,
            "FlyingSnowVelvet-LTS2-Offline-Installer.zip",
            "modelscope",
            "ModelScope",
            "same",
            0.1,
            ("huggingface",),
        )
        manager = UpdateManager()
        with patch.object(
            manager,
            "_download_url",
            side_effect=[UpdateError("modelscope unavailable"), None],
        ) as download:
            manager._download_release(release, Path("package.zip"))

        self.assertEqual(
            [call.args[0] for call in download.call_args_list], ["modelscope", "huggingface"]
        )

    def test_matching_timestamp_is_already_the_latest_package(self):
        published = datetime(2026, 7, 29, tzinfo=timezone.utc)
        release = ReleaseInfo(
            "LTS2", published, "FlyingSnowVelvet-LTS2-Offline-Installer.zip", "download",
            "ModelScope", "new", 0.1, (), "a" * 64,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                json.dumps({
                    "version": "LTS2",
                    "installed_at": "2026-07-29T00:00:00Z",
                    "revision": "old",
                }),
                encoding="utf-8",
            )
            manager = UpdateManager(state_path=state_path)
            with patch.object(manager, "_fetch_latest_release", return_value=release):
                result = manager.check_for_updates()

        self.assertFalse(result.update_available)
        self.assertEqual(result.reason, "up_to_date")
        self.assertIsInstance(result.installed_state, InstalledState)

    def test_local_build_newer_than_the_published_package_is_up_to_date(self):
        published = datetime(2026, 9, 12, tzinfo=timezone.utc)
        release = ReleaseInfo(
            "LTS1.0.7pre3",
            published,
            "FlyingSnowVelvet-LTS1.0.7pre3-Offline-Installer.zip",
            "download",
            "Hugging Face",
            "remote-revision",
            0.1,
            (),
            "a" * 64,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                json.dumps({
                    "version": "LTS1.0.7pre4",
                    "installed_at": "2026-09-13T00:00:00Z",
                    "revision": "local-revision",
                }),
                encoding="utf-8",
            )
            manager = UpdateManager(state_path=state_path)
            with patch.object(manager, "_fetch_latest_release", return_value=release):
                result = manager.check_for_updates()

        self.assertFalse(result.update_available)
        self.assertEqual(result.reason, "up_to_date")

    def test_same_version_without_local_revision_is_not_reoffered(self):
        published = datetime(2026, 9, 7, tzinfo=timezone.utc)
        release = ReleaseInfo(
            "LTS1.0.7pre2", published, "resources.zip", "download",
            "ModelScope", "c8f0337", archive_sha256="a" * 64, kind="resources"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(json.dumps({
                "version": "LTS1.0.7pre2",
                "installed_at": "2026-09-06T00:00:00Z",
            }), encoding="utf-8")
            manager = UpdateManager(state_path=state_path)
            with patch.object(manager, "_fetch_latest_release", return_value=release):
                result = manager.check_for_updates()
        self.assertFalse(result.update_available)

    def test_same_version_revision_change_is_an_update(self):
        published = datetime(2026, 9, 7, tzinfo=timezone.utc)
        release = ReleaseInfo(
            "LTS1.0.7pre2", published, "resources.zip", "download",
            "ModelScope", "new", archive_sha256="a" * 64, kind="resources"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(json.dumps({
                "version": "LTS1.0.7pre2",
                "installed_at": "2026-09-06T00:00:00Z",
                "revision": "old",
            }), encoding="utf-8")
            manager = UpdateManager(state_path=state_path)
            with patch.object(manager, "_fetch_latest_release", return_value=release):
                result = manager.check_for_updates()
        self.assertTrue(result.update_available)


    def test_probe_starts_every_source_at_once_and_honours_the_budget(self):
        available = ReleaseInfo(
            "LTS2",
            datetime(2026, 7, 29, tzinfo=timezone.utc),
            "FlyingSnowVelvet-LTS2-Offline-Installer.zip",
            "download",
            "Hugging Face",
            "revision",
            0.1,
            (),
            "a" * 64,
        )

        def stall(deadline=None):
            time.sleep(1.5)
            raise UpdateError("stalled source")

        manager = UpdateManager()
        with (
            patch.object(manager, "_fetch_huggingface_voice_release", return_value=available),
            patch.object(manager, "_fetch_modelscope_voice_release", side_effect=stall),
            patch("lib.script.update_manager._PROBE_BUDGET_SECONDS", 0.8),
            patch("lib.script.update_manager._PROBE_GRACE_SECONDS", 5.0),
        ):
            started = time.monotonic()
            selected = manager._fetch_latest_release()
            elapsed = time.monotonic() - started

        self.assertEqual(selected, available)
        self.assertLess(elapsed, 1.5)

    def test_probe_only_asks_the_two_model_hubs(self):
        older = datetime(2026, 7, 29, tzinfo=timezone.utc)
        newer = datetime(2026, 8, 2, tzinfo=timezone.utc)
        manifest = ReleaseInfo(
            "LTS2", older, "hf.zip", "hf", "Hugging Face", "r1", 0.1, (), "a" * 64
        )
        modelscope = ReleaseInfo(
            "LTS3", newer, "ms.zip", "ms", "ModelScope", "r2", 0.2, (), "b" * 64
        )
        manager = UpdateManager()
        with (
            patch.object(manager, "_fetch_huggingface_voice_release", return_value=manifest) as hf,
            patch.object(manager, "_fetch_modelscope_voice_release", return_value=modelscope) as ms,
        ):
            selected = manager._fetch_latest_release()

        self.assertEqual(selected.source, "ModelScope")
        self.assertEqual(selected.tag, "LTS3")
        self.assertEqual(hf.call_count, 1)
        self.assertEqual(ms.call_count, 1)
        self.assertFalse(hasattr(UpdateManager, "_fetch_github_pack_release"))
        self.assertFalse(hasattr(UpdateManager, "_fetch_gitee_pack_release"))

    def test_probe_gives_up_inside_the_budget_when_every_source_stalls(self):
        def stall(deadline=None):
            time.sleep(1.5)
            raise UpdateError("stalled source")

        manager = UpdateManager()
        with (
            patch.object(manager, "_fetch_huggingface_voice_release", side_effect=stall),
            patch.object(manager, "_fetch_modelscope_voice_release", side_effect=stall),
            patch("lib.script.update_manager._PROBE_BUDGET_SECONDS", 0.8),
        ):
            started = time.monotonic()
            with self.assertRaises(UpdateError):
                manager._fetch_latest_release()
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.5)

    def test_manifest_probe_is_skipped_once_the_budget_is_spent(self):
        with patch("lib.script.update_manager.requests.get") as request:
            with self.assertRaises(UpdateError):
                UpdateManager._fetch_release_json(
                    "https://example.test/updates/latest.json",
                    "测试源",
                    deadline=time.monotonic() - 1.0,
                )
        request.assert_not_called()

    def test_update_notice_reports_the_version_not_the_file_name(self):
        published = datetime(2026, 9, 7, tzinfo=timezone.utc)
        release = ReleaseInfo(
            "LTS1.0.8",
            published,
            "FlyingSnowVelvet-LTS1.0.8-Offline-Installer.zip",
            "download",
            "ModelScope",
            "revision",
            archive_sha256="a" * 64,
        )
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                json.dumps({
                    "version": "LTS1.0.7pre3",
                    "installed_at": "2026-09-06T00:00:00Z",
                    "revision": "old",
                }),
                encoding="utf-8",
            )
            manager = UpdateManager(state_path=state_path, info_callback=messages.append)
            with patch.object(manager, "_fetch_latest_release", return_value=release):
                result = manager.check_for_updates()

        self.assertTrue(result.update_available)
        notice = " ".join(messages)
        self.assertIn("LTS1.0.8", notice)
        self.assertNotIn("Offline-Installer.zip", notice)


class StreamingDownloadHashTests(unittest.TestCase):
    """下载时随流算 SHA-256：更新包只需要核对这一个哈希。"""

    def test_download_returns_the_streamed_digest(self):
        payload = b"fsv-update-payload" * 90000
        expected = hashlib.sha256(payload).hexdigest()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 的接口名
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):  # pragma: no cover - 测试里不需要访问日志
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                destination = Path(temp_dir) / "FlyingSnowVelvet-LTS2-Resources.zip"
                progress: list[str] = []
                manager = UpdateManager(
                    progress_callback=lambda _c, _t, message: progress.append(message)
                )
                digest = manager._download_url(
                    f"http://127.0.0.1:{server.server_port}/resources.zip", destination
                )

                self.assertEqual(digest, expected)
                self.assertEqual(destination.read_bytes(), payload)
                self.assertTrue(progress)
                self.assertTrue(all("下载" in message for message in progress))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
