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
from scripts.publish_offline_installer import (
    _old_update_paths,
    prepare_release,
    prepare_resource_release,
)


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

    def test_prepare_resource_release_records_install_estimate(self):
        # 在线安装器在下载前先读这两个字段做磁盘空间预估；资源 ZIP 的真实
        # 目录结构由原生解压器在下载后复核。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resource = root / "FlyingSnowVelvet-LTS2-Resources.zip"
            with zipfile.ZipFile(resource, "w") as archive:
                archive.writestr("app/a.txt", "alpha")
                archive.writestr("runtime/b.bin", b"bravo" * 16)
                archive.writestr("empty/", b"")

            target, metadata_path = prepare_resource_release(
                resource,
                version="LTS2",
                revision="commit-123",
                published_at="2026-09-06T00:00:00Z",
                output_dir=root / "publish",
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

            self.assertEqual(metadata["kind"], "resources")
            self.assertEqual(metadata["asset_path"], "updates/" + target.name)
            self.assertEqual(metadata["file_count"], 2)
            self.assertEqual(metadata["uncompressed_bytes"], 5 + 80)
            self.assertEqual(metadata["size"], target.stat().st_size)
            self.assertEqual(
                metadata["sha256"], hashlib.sha256(target.read_bytes()).hexdigest()
            )


if __name__ == "__main__":
    unittest.main()
