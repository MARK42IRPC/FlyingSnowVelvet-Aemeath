"""Wrap and publish the offline installer to the voice-package hubs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from config.version_info import (
    OFFLINE_UPDATE_FORMAT,
    OFFLINE_UPDATE_METADATA_PATH,
    VOICE_PACKAGE_HUGGINGFACE_REPO,
    VOICE_PACKAGE_MODELSCOPE_REPO,
)
from lib.script.app.update_installer import validate_update_installer

_VERSION_RE = re.compile(r"[A-Za-z0-9._+-]+")
_HF_TOKEN_RE = re.compile(r"^hf_[A-Za-z0-9]+$")
_MODELSCOPE_TOKEN_RE = re.compile(r"^ms-[A-Za-z0-9-]+$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _isoformat(value: str | None) -> str:
    if value:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_tokens(path: Path | None) -> tuple[str, str]:
    values = []
    if path is not None:
        values.extend(path.read_text(encoding="utf-8").splitlines())
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or ""
    modelscope_token = os.environ.get("MODELSCOPE_TOKEN") or os.environ.get("MODELSCOPE_API_TOKEN") or ""
    for value in values:
        token = value.strip()
        if not hf_token and _HF_TOKEN_RE.fullmatch(token):
            hf_token = token
        if not modelscope_token and _MODELSCOPE_TOKEN_RE.fullmatch(token):
            modelscope_token = token
    return hf_token, modelscope_token


def _release_paths(output_dir: Path, version: str) -> tuple[Path, Path]:
    asset_name = f"FlyingSnowVelvet-{version}-Offline-Installer.zip"
    asset_path = output_dir / "updates" / asset_name
    metadata_path = output_dir / Path(OFFLINE_UPDATE_METADATA_PATH)
    return asset_path, metadata_path


def prepare_release(
    installer: Path,
    *,
    version: str,
    revision: str,
    published_at: str | None,
    output_dir: Path,
    manifest: Path | None = None,
) -> tuple[Path, Path]:
    installer = installer.resolve()
    output_dir = output_dir.resolve()
    if not installer.is_file() or installer.suffix.casefold() != ".exe":
        raise SystemExit(f"缺少离线安装器 EXE：{installer}")
    if not _VERSION_RE.fullmatch(version):
        raise SystemExit(f"版本号不能用于发布文件名：{version!r}")
    validate_update_installer(installer)
    asset_path, metadata_path = _release_paths(output_dir, version)
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_asset = asset_path.with_suffix(asset_path.suffix + ".part")
    with zipfile.ZipFile(temporary_asset, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.write(installer, installer.name)
    os.replace(temporary_asset, asset_path)
    if manifest is not None:
        manifest = manifest.resolve()
        if not manifest.is_file():
            raise SystemExit(f"发行版 manifest 不存在：{manifest}")
        target_manifest = asset_path.parent / f"FlyingSnowVelvet-{version}-manifest.json"
        target_manifest.write_bytes(manifest.read_bytes())
    metadata = {
        "format": OFFLINE_UPDATE_FORMAT,
        "version": version,
        "published_at": _isoformat(published_at),
        "revision": revision.strip() or f"sha256:{_sha256(asset_path)}",
        "asset_name": asset_path.name,
        "asset_path": asset_path.relative_to(output_dir).as_posix(),
        "size": asset_path.stat().st_size,
        "sha256": _sha256(asset_path),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return asset_path, metadata_path


def _publication_files(asset_path: Path, metadata_path: Path) -> tuple[Path, ...]:
    manifest_path = asset_path.with_name(
        asset_path.name.replace("-Offline-Installer.zip", "-manifest.json")
    )
    files = [asset_path]
    if manifest_path.is_file():
        files.append(manifest_path)
    files.append(metadata_path)
    return tuple(files)


def _publish_huggingface(asset_path: Path, metadata_path: Path, output_dir: Path, token: str) -> None:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    for local_path in _publication_files(asset_path, metadata_path):
        api.upload_file(
            path_or_fileobj=str(local_path),
            path_in_repo=local_path.relative_to(output_dir).as_posix(),
            repo_id=VOICE_PACKAGE_HUGGINGFACE_REPO,
            repo_type="model",
            revision="main",
            commit_message=f"Publish desktop installer {asset_path.stem}",
        )


def _publish_modelscope(asset_path: Path, metadata_path: Path, output_dir: Path, token: str) -> None:
    from modelscope.hub.api import HubApi

    api = HubApi(token=token)
    for local_path in _publication_files(asset_path, metadata_path):
        api.upload_file(
            repo_id=VOICE_PACKAGE_MODELSCOPE_REPO,
            path_or_fileobj=str(local_path),
            path_in_repo=local_path.relative_to(output_dir).as_posix(),
            revision="master",
            commit_message=f"Publish desktop installer {asset_path.stem}",
        )


def publish_release(asset_path: Path, metadata_path: Path, output_dir: Path, *, hf_token: str, modelscope_token: str) -> None:
    if not hf_token or not modelscope_token:
        raise SystemExit("发布离线安装器需要 HF_TOKEN 与 MODELSCOPE_TOKEN")
    _publish_huggingface(asset_path, metadata_path, output_dir, hf_token)
    _publish_modelscope(asset_path, metadata_path, output_dir, modelscope_token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--published-at")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    asset_path, metadata_path = prepare_release(
        args.installer,
        version=args.version,
        revision=args.revision,
        published_at=args.published_at,
        output_dir=args.output_dir,
        manifest=args.manifest,
    )
    print(f"已准备外层安装器 ZIP：{asset_path}")
    print(f"已生成更新清单：{metadata_path}")
    if not args.prepare_only:
        hf_token, modelscope_token = _read_tokens(args.token_file)
        publish_release(
            asset_path,
            metadata_path,
            args.output_dir.resolve(),
            hf_token=hf_token,
            modelscope_token=modelscope_token,
        )
        print("已发布到 Hugging Face 与 ModelScope 语音包仓库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
