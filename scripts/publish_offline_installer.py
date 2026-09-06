"""Wrap and publish the offline installer to the voice-package hubs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
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
_ASSET_NAME_RE = re.compile(
    r"^FlyingSnowVelvet-(?P<version>[A-Za-z0-9._+-]+)-Offline-Installer\.zip$"
)
_MANIFEST_NAME_RE = re.compile(
    r"^FlyingSnowVelvet-(?P<version>[A-Za-z0-9._+-]+)-manifest\.json$"
)
_RESOURCE_NAME_RE = re.compile(
    r"^FlyingSnowVelvet-(?P<version>[A-Za-z0-9._+-]+)-Resources\.zip$"
)


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
        # Atoken files commonly annotate credentials as ``label：token`` or
        # ``label=token``; accept the token portion without logging it.
        if "：" in token:
            token = token.rsplit("：", 1)[1].strip()
        elif "=" in token and not token.startswith(("hf_", "ms-")):
            token = token.rsplit("=", 1)[1].strip()
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


def _validate_local_release(asset_path: Path, metadata_path: Path) -> dict[str, object]:
    """Validate the generated ZIP and latest.json before any remote upload."""
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit(f"本地发布清单无法读取：{metadata_path}") from exc
    if not isinstance(metadata, dict):
        raise SystemExit("本地发布清单必须是 JSON 对象")
    match = _ASSET_NAME_RE.fullmatch(asset_path.name)
    if match is None:
        raise SystemExit(f"发布 ZIP 文件名无效：{asset_path.name}")
    version = match.group("version")
    expected_asset_name = asset_path.name
    actual_size = asset_path.stat().st_size
    actual_sha256 = _sha256(asset_path)
    if (
        metadata.get("format") != OFFLINE_UPDATE_FORMAT
        or metadata.get("version") != version
        or metadata.get("asset_name") != expected_asset_name
        or metadata.get("asset_path") != f"{OFFLINE_UPDATE_METADATA_PATH.rsplit('/', 1)[0]}/{asset_path.name}"
        or int(metadata.get("size", -1)) != actual_size
        or str(metadata.get("sha256", "")).lower() != actual_sha256
    ):
        raise SystemExit("本地安装器 ZIP 与 latest.json 的大小或 SHA-256 不一致")
    with zipfile.ZipFile(asset_path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        expected_installer = asset_path.name.replace(
            "-Offline-Installer.zip",
            "-Offline-Installer.exe",
        )
        if len(members) != 1 or members[0].filename != expected_installer:
            raise SystemExit("本地安装器 ZIP 必须只包含清单指定的 EXE")
    return metadata


def _validate_release_manifest(manifest_path: Path, version: str) -> None:
    """Reject a payload manifest from a different release before publishing."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit(f"发行版 manifest 无法读取：{manifest_path}") from exc
    if not isinstance(manifest, dict) or str(manifest.get("version", "")).strip() != version:
        raise SystemExit(f"发行版 manifest 版本与发布版本不一致：{manifest_path}")


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
    expected_name = f"FlyingSnowVelvet-{version}-Offline-Installer.exe"
    if installer.name != expected_name:
        raise SystemExit(f"安装器文件名与版本不一致：需要 {expected_name}，实际为 {installer.name}")
    temporary_asset = asset_path.with_suffix(asset_path.suffix + ".part")
    try:
        with zipfile.ZipFile(temporary_asset, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.write(installer, installer.name)
        os.replace(temporary_asset, asset_path)
    finally:
        temporary_asset.unlink(missing_ok=True)
    if manifest is not None:
        manifest = manifest.resolve()
        if not manifest.is_file():
            raise SystemExit(f"发行版 manifest 不存在：{manifest}")
        _validate_release_manifest(manifest, version)
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
    metadata = _validate_local_release(asset_path, metadata_path)
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


def prepare_resource_release(
    resource_archive: Path,
    *,
    version: str,
    revision: str,
    published_at: str | None,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Prepare a desktop/runtime resource ZIP and its update metadata.

    Resource bundles are the only files uploaded for online updates; EXE files
    remain local build artifacts.  The ZIP is checked for traversal entries
    before metadata is emitted.
    """
    source = Path(resource_archive).resolve()
    if not source.is_file() or source.suffix.casefold() != ".zip":
        raise SystemExit(f"缺少资源包 ZIP：{source}")
    if not _VERSION_RE.fullmatch(version):
        raise SystemExit(f"版本号不能用于发布文件名：{version!r}")
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            normalized = str(member.filename or "").replace("\\", "/")
            path = Path(normalized)
            if path.is_absolute() or ".." in path.parts:
                raise SystemExit(f"资源包包含不安全路径：{member.filename}")
    output_dir = Path(output_dir).resolve()
    target = output_dir / "updates" / f"FlyingSnowVelvet-{version}-Resources.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    metadata_path = output_dir / Path(OFFLINE_UPDATE_METADATA_PATH)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "format": OFFLINE_UPDATE_FORMAT,
        "version": version,
        "published_at": _isoformat(published_at),
        "revision": revision.strip() or f"sha256:{_sha256(target)}",
        "asset_name": target.name,
        "asset_path": target.relative_to(output_dir).as_posix(),
        "size": target.stat().st_size,
        "sha256": _sha256(target),
        "kind": "resources",
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target, metadata_path


def _old_update_paths(remote_paths: list[str], current_version: str) -> list[str]:
    """Return only obsolete installer/manifest files under the update slot."""
    obsolete: list[str] = []
    for raw_path in remote_paths:
        path = str(raw_path or "").replace("\\", "/")
        if not path.startswith("updates/") or path.count("/") != 1:
            continue
        name = path.rsplit("/", 1)[1]
        match = _ASSET_NAME_RE.fullmatch(name) or _MANIFEST_NAME_RE.fullmatch(name) or _RESOURCE_NAME_RE.fullmatch(name)
        if match is not None and match.group("version") != current_version:
            obsolete.append(path)
    return sorted(set(obsolete))


def _retry_upload(callback, description: str) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            callback()
            return
        except Exception as exc:  # SDKs expose different transient exception types.
            last_error = exc
            if attempt == 3:
                break
            time.sleep(float(attempt))
    raise RuntimeError(f"上传 {description} 失败（已重试 3 次）：{last_error}") from last_error


def _publish_huggingface(
    asset_path: Path,
    metadata_path: Path,
    output_dir: Path,
    token: str,
    *,
    files: tuple[Path, ...] | None = None,
) -> None:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    for local_path in files or _publication_files(asset_path, metadata_path):
        path_in_repo = local_path.relative_to(output_dir).as_posix()
        _retry_upload(
            lambda: api.upload_file(
                path_or_fileobj=str(local_path),
                path_in_repo=path_in_repo,
                repo_id=VOICE_PACKAGE_HUGGINGFACE_REPO,
                repo_type="model",
                revision="main",
                commit_message=f"Publish desktop installer {asset_path.stem}",
            ),
            f"Hugging Face/{path_in_repo}",
        )


def _publish_modelscope(
    asset_path: Path,
    metadata_path: Path,
    output_dir: Path,
    token: str,
    *,
    files: tuple[Path, ...] | None = None,
) -> None:
    from modelscope.hub.api import HubApi

    api = HubApi(token=token)
    for local_path in files or _publication_files(asset_path, metadata_path):
        path_in_repo = local_path.relative_to(output_dir).as_posix()
        _retry_upload(
            lambda: api.upload_file(
                repo_id=VOICE_PACKAGE_MODELSCOPE_REPO,
                path_or_fileobj=str(local_path),
                path_in_repo=path_in_repo,
                revision="master",
                commit_message=f"Publish desktop installer {asset_path.stem}",
            ),
            f"ModelScope/{path_in_repo}",
        )


def _cleanup_huggingface_old_releases(token: str, current_version: str) -> list[str]:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    remote_paths = api.list_repo_files(
        repo_id=VOICE_PACKAGE_HUGGINGFACE_REPO,
        repo_type="model",
        revision="main",
        token=token,
    )
    obsolete = _old_update_paths(list(remote_paths), current_version)
    for path in obsolete:
        _retry_upload(
            lambda path=path: api.delete_file(
                path_in_repo=path,
                repo_id=VOICE_PACKAGE_HUGGINGFACE_REPO,
                repo_type="model",
                revision="main",
                token=token,
                commit_message=f"Remove obsolete installer {path}",
            ),
            f"Hugging Face/{path}",
        )
    return obsolete


def _cleanup_modelscope_old_releases(token: str, current_version: str) -> list[str]:
    """Best-effort cleanup; ModelScope may require a cookie session for DELETE."""
    try:
        from modelscope_hub import HubApi
    except ImportError:
        return []
    api = HubApi(token=token)
    remote_files = api.list_repo_files(
        VOICE_PACKAGE_MODELSCOPE_REPO,
        "model",
        revision="master",
        recursive=True,
    )
    obsolete = _old_update_paths(
        [str(getattr(item, "path", item)) for item in remote_files],
        current_version,
    )
    if obsolete:
        result = api.delete_files(
            VOICE_PACKAGE_MODELSCOPE_REPO,
            "model",
            obsolete,
            revision="master",
        )
        failed = [str(item) for item in (result or {}).get("failed_files", [])]
        if failed:
            raise RuntimeError("ModelScope 未删除文件：" + ", ".join(failed))
    return obsolete


def cleanup_old_releases(*, hf_token: str, modelscope_token: str, current_version: str) -> None:
    """Remove obsolete update-slot files without touching model archives."""
    try:
        removed = _cleanup_huggingface_old_releases(hf_token, current_version)
        if removed:
            print("已清理 Hugging Face 旧安装器：" + ", ".join(removed))
    except Exception as exc:
        print(f"警告：Hugging Face 旧安装器清理失败，保留远端文件：{exc}", file=sys.stderr)
    try:
        removed = _cleanup_modelscope_old_releases(modelscope_token, current_version)
        if removed:
            print("已清理 ModelScope 旧安装器：" + ", ".join(removed))
    except Exception as exc:
        print(
            "警告：ModelScope 旧安装器清理失败（API token 可能不具备删除权限），"
            f"保留远端文件：{exc}",
            file=sys.stderr,
        )


def publish_release(asset_path: Path, metadata_path: Path, output_dir: Path, *, hf_token: str, modelscope_token: str) -> None:
    if not hf_token or not modelscope_token:
        raise SystemExit("发布离线安装器需要 HF_TOKEN 与 MODELSCOPE_TOKEN")
    try:
        raw_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"发布清单无法读取：{metadata_path}") from exc
    if isinstance(raw_metadata, dict) and raw_metadata.get("kind") == "resources":
        metadata = raw_metadata
    else:
        metadata = _validate_local_release(asset_path, metadata_path)
    publication_files = (asset_path, metadata_path)
    if not (isinstance(metadata, dict) and metadata.get("kind") == "resources"):
        publication_files = _publication_files(asset_path, metadata_path)
    # Remote model hubs now carry resource ZIPs and manifests only; installer
    # EXEs remain local release artifacts.  Keep metadata publication intact.
    resource_files = tuple(
        path for path in output_dir.joinpath("updates").glob("*-Resources.zip")
        if path.is_file()
    )
    immutable_files = tuple(resource_files)
    # Both mirrors receive immutable payloads before either latest pointer is
    # moved. A retry can then safely continue without rebuilding the archive.
    if immutable_files:
        _publish_huggingface(asset_path, metadata_path, output_dir, hf_token, files=immutable_files)
        _publish_modelscope(asset_path, metadata_path, output_dir, modelscope_token, files=immutable_files)
    _publish_huggingface(
        asset_path, metadata_path, output_dir, hf_token, files=(metadata_path,)
    )
    _publish_modelscope(
        asset_path, metadata_path, output_dir, modelscope_token, files=(metadata_path,)
    )
    cleanup_old_releases(
        hf_token=hf_token,
        modelscope_token=modelscope_token,
        current_version=str(metadata.get("version", "")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--resource", type=Path, help="桌宠与 runtime 资源包 ZIP（在线版发布）")
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--published-at")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    if args.resource:
        asset_path, metadata_path = prepare_resource_release(
            args.resource,
            version=args.version,
            revision=args.revision,
            published_at=args.published_at,
            output_dir=args.output_dir,
        )
    else:
        if not args.installer:
            raise SystemExit("需要 --installer 或 --resource")
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
