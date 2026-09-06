from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from lib.script.update_manager import (
    InstalledState,
    ReleaseInfo,
    UpdateError,
    UpdateManager,
    _GITHUB_PACK_API,
    _GITHUB_PACK_REF_API,
    _GITEE_PACK_API,
    _GITEE_PACK_PAGE,
    _extract_gitee_attachments,
    _extract_gitee_revision,
    _parse_voice_package_release,
    _select_release_source,
    _select_installer_asset,
    _is_retryable_request_error,
)


def _response(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class UpdateManagerReleaseSelectionTests(unittest.TestCase):
    def test_only_transient_http_errors_are_retried(self):
        response = Mock(status_code=405)
        self.assertFalse(_is_retryable_request_error(requests.HTTPError(response=response)))
        response.status_code = 503
        self.assertTrue(_is_retryable_request_error(requests.HTTPError(response=response)))
        self.assertTrue(_is_retryable_request_error(requests.ConnectionError("reset")))

    def test_installer_asset_requires_a_download_url(self):
        selected = _select_installer_asset([
            {"name": "manifest.json", "browser_download_url": "manifest"},
            {"name": "broken.exe"},
            {"name": "FlyingSnowVelvet-LTS2-Offline-Installer.exe", "browser_download_url": "package"},
        ])
        self.assertEqual(selected["name"], "FlyingSnowVelvet-LTS2-Offline-Installer.exe")

    def test_zip_and_green_assets_are_rejected(self):
        selected = _select_installer_asset(
            [
                {"name": "最新包.zip", "browser_download_url": "source"},
                {"name": "FlyingSnowVelvet-LTS2-green.zip", "browser_download_url": "green"},
            ],
            "最新包",
        )
        self.assertIsNone(selected)

    def test_versioned_installer_is_preferred_for_release_tag(self):
        selected = _select_installer_asset(
            [
                {"name": "FlyingSnowVelvet-other-Offline-Installer.exe", "browser_download_url": "other"},
                {"name": "FlyingSnowVelvet-LTS2-Offline-Installer.exe", "browser_download_url": "package"},
            ],
            "LTS2",
        )
        self.assertEqual(selected["browser_download_url"], "package")

    def test_github_requires_a_real_installer_asset(self):
        payload = {
            "id": 123,
            "tag_name": "PACK",
            "updated_at": "2026-07-28T16:19:59Z",
            "assets": [{
                "name": "FlyingSnowVelvet-LTS2-Offline-Installer.exe",
                "browser_download_url": "https://example.test/installer.exe",
            }],
            "zipball_url": "https://example.test/github-pack.zip",
        }
        tag_ref = {"object": {"sha": "abc123"}}
        with patch(
            "lib.script.update_manager.requests.get",
            side_effect=[_response(payload), _response(tag_ref)],
        ) as get:
            release = UpdateManager._fetch_github_pack_release()

        self.assertEqual(get.call_args_list[0].args[0], _GITHUB_PACK_API)
        self.assertEqual(get.call_args_list[1].args[0], _GITHUB_PACK_REF_API)
        self.assertEqual(release.tag, "PACK")
        self.assertEqual(release.source, "GitHub")
        self.assertEqual(release.revision, "abc123")
        self.assertEqual(release.download_url, payload["assets"][0]["browser_download_url"])

    def test_gitee_uses_fixed_latest_package_release_asset(self):
        payload = {
            "id": 456,
            "tag_name": "最新包",
            "target_commitish": "abc123",
            "created_at": "2026-07-29T00:21:25+08:00",
            "assets": [{
                "name": "FlyingSnowVelvet-最新包-Offline-Installer.exe",
                "browser_download_url": "https://example.test/gitee-pack.zip",
            }],
        }
        with patch(
            "lib.script.update_manager.requests.get",
            side_effect=[_response(payload), _response({})],
        ) as get:
            release = UpdateManager._fetch_gitee_pack_release()

        self.assertEqual(get.call_args_list[0].args[0], _GITEE_PACK_API)
        self.assertEqual(get.call_args_list[1].args[0], _GITEE_PACK_PAGE)
        self.assertEqual(release.source, "Gitee")
        self.assertEqual(release.revision, "abc123")
        self.assertEqual(release.download_url, "https://example.test/gitee-pack.zip")

    def test_gitee_page_attachment_is_normalized_and_preferred(self):
        page_data = {
            "release": {
                "release": {
                    "attach_files": [{
                        "name": "FlyingSnowVelvet-LTS2-Offline-Installer.exe",
                        "download_url": "/downloads/real-package.zip",
                    }]
                }
            }
        }
        self.assertEqual(
            _extract_gitee_attachments(page_data),
            [{
                "name": "FlyingSnowVelvet-LTS2-Offline-Installer.exe",
                "browser_download_url": "https://gitee.com/downloads/real-package.zip",
            }],
        )

    def test_gitee_page_revision_overrides_stale_release_api_target(self):
        self.assertEqual(
            _extract_gitee_revision({
                "release": {
                    "tag": {
                        "commit": {"id": "current-tag-commit"},
                    }
                }
            }),
            "current-tag-commit",
        )

    def test_newer_release_wins_before_network_latency(self):
        older_fast = ReleaseInfo(
            "PACK",
            datetime(2026, 7, 28, tzinfo=timezone.utc),
            "github.exe",
            "github",
            "GitHub",
            "github:1",
            0.1,
        )
        newer_slow = ReleaseInfo(
            "最新包",
            datetime(2026, 7, 29, tzinfo=timezone.utc),
            "gitee.exe",
            "gitee",
            "Gitee",
            "gitee:1",
            2.0,
        )
        self.assertIs(_select_release_source([older_fast, newer_slow]), newer_slow)

    def test_equal_release_time_uses_faster_source(self):
        published = datetime(2026, 7, 29, tzinfo=timezone.utc)
        slow = ReleaseInfo("PACK", published, "a.exe", "a", "GitHub", "a", 1.5)
        fast = ReleaseInfo("最新包", published, "b.exe", "b", "Gitee", "b", 0.2)
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
        github = ReleaseInfo(
            "LTS2", published, "github.zip", "github", "Hugging Face", "same", 0.2, (), "a" * 64
        )
        gitee = ReleaseInfo(
            "LTS2", published, "gitee.zip", "gitee", "ModelScope", "same", 0.1, (), "a" * 64
        )
        manager = UpdateManager()
        with (
            patch.object(manager, "_fetch_huggingface_voice_release", return_value=github),
            patch.object(manager, "_fetch_modelscope_voice_release", return_value=gitee),
        ):
            selected = manager._fetch_latest_release()

        self.assertEqual(selected.source, "ModelScope")
        self.assertEqual(selected.fallback_download_urls, ("github",))

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
            "最新包",
            published,
            "package.exe",
            "gitee",
            "Gitee",
            "same",
            0.1,
            ("github",),
        )
        manager = UpdateManager()
        with patch.object(
            manager,
            "_download_url",
            side_effect=[UpdateError("gitee unavailable"), None],
        ) as download:
            manager._download_release(release, Path("package.exe"))

        self.assertEqual([call.args[0] for call in download.call_args_list], ["gitee", "github"])

    def test_revision_change_is_an_update_even_when_timestamp_matches(self):
        published = datetime(2026, 7, 29, tzinfo=timezone.utc)
        release = ReleaseInfo(
            "PACK", published, "pack.exe", "download", "GitHub", "new", 0.1
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                json.dumps({
                    "version": "PACK",
                    "installed_at": "2026-07-29T00:00:00Z",
                    "revision": "old",
                }),
                encoding="utf-8",
            )
            manager = UpdateManager(state_path=state_path)
            with patch.object(manager, "_fetch_latest_release", return_value=release):
                result = manager.check_for_updates()

        self.assertTrue(result.update_available)
        self.assertIsInstance(result.installed_state, InstalledState)


if __name__ == "__main__":
    unittest.main()
