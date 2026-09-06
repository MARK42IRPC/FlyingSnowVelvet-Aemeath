from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from lib.script.app.update_installer import (
    OFFLINE_INSTALLER_MAGIC,
    OFFLINE_INSTALLER_TRAILER_FORMAT,
    validate_update_installer,
)
from scripts.publish_offline_installer import _old_update_paths, prepare_release


def _write_installer(path: Path) -> None:
    payload = b""
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as buffer:
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(".fsv-install-root", "marker\n")
        buffer.seek(0)
        payload = buffer.read()
    path.write_bytes(
        b"MZstub"
        + payload
        + struct.pack(
            OFFLINE_INSTALLER_TRAILER_FORMAT,
            OFFLINE_INSTALLER_MAGIC,
            len(payload),
            hashlib.sha256(payload).digest(),
        )
    )


class PublishOfflineInstallerTests(unittest.TestCase):
    def test_old_update_paths_are_limited_to_versioned_update_assets(self):
        paths = _old_update_paths(
            [
                "updates/FlyingSnowVelvet-LTS1-Offline-Installer.zip",
                "updates/FlyingSnowVelvet-LTS1-manifest.json",
                "updates/FlyingSnowVelvet-LTS2-Offline-Installer.zip",
                "updates/latest.json",
                "Aemeath_ONNX_GSV_Complete_FP32.rar",
                "updates/readme.txt",
            ],
            "LTS2",
        )
        self.assertEqual(
            paths,
            [
                "updates/FlyingSnowVelvet-LTS1-Offline-Installer.zip",
                "updates/FlyingSnowVelvet-LTS1-manifest.json",
            ],
        )

    def test_prepare_release_creates_outer_zip_and_latest_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            installer = root / "FlyingSnowVelvet-LTS2-Offline-Installer.exe"
            _write_installer(installer)
            asset, metadata = prepare_release(
                installer,
                version="LTS2",
                revision="commit-123",
                published_at="2026-09-06T00:00:00Z",
                output_dir=root / "publish",
            )

            self.assertEqual(asset.name, "FlyingSnowVelvet-LTS2-Offline-Installer.zip")
            with zipfile.ZipFile(asset) as archive:
                self.assertEqual(archive.namelist(), [installer.name])
            data = json.loads(metadata.read_text(encoding="utf-8"))
            self.assertEqual(data["format"], "fsv-offline-installer-v1")
            self.assertEqual(data["asset_path"], "updates/" + asset.name)
            self.assertEqual(data["revision"], "commit-123")
            self.assertEqual(data["sha256"], hashlib.sha256(asset.read_bytes()).hexdigest())
            validate_update_installer(installer)

    def test_prepare_release_rejects_mismatched_installer_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            installer = root / "wrong-name.exe"
            _write_installer(installer)
            with self.assertRaises(SystemExit):
                prepare_release(
                    installer,
                    version="LTS2",
                    revision="commit-123",
                    published_at="2026-09-06T00:00:00Z",
                    output_dir=root / "publish",
                )

    def test_prepare_release_rejects_manifest_from_another_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            installer = root / "FlyingSnowVelvet-LTS2-Offline-Installer.exe"
            _write_installer(installer)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"version": "LTS1"}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                prepare_release(
                    installer,
                    version="LTS2",
                    revision="commit-123",
                    published_at="2026-09-06T00:00:00Z",
                    output_dir=root / "publish",
                    manifest=manifest,
                )


if __name__ == "__main__":
    unittest.main()
