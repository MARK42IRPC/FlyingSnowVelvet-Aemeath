"""GitHub 分发更新与开发版 Git 同步管理器。"""

from __future__ import annotations

import json
import hashlib
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import quote, urljoin

import requests

from config.version_info import (
    GITHUB_REPO,
    OFFLINE_UPDATE_FORMAT,
    OFFLINE_UPDATE_METADATA_PATH,
    RESOURCE_RELEASE_DATE,
    RESOURCE_VERSION,
    VOICE_PACKAGE_HUGGINGFACE_REPO,
    VOICE_PACKAGE_MODELSCOPE_REPO,
)
from lib.core.logger import get_logger

_logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATE_PATH = _PROJECT_ROOT / "resc" / "user" / "update_state.json"
_STAGING_ROOT = Path(tempfile.gettempdir()) / "FlyingSnowVelvet" / "updates"
_GITHUB_PACK_API = (
    "https://api.github.com/repos/MARK42IRPC/"
    "FlyingSnowVelvet-Aemeath/releases/tags/PACK"
)
_GITHUB_PACK_REF_API = (
    "https://api.github.com/repos/MARK42IRPC/"
    "FlyingSnowVelvet-Aemeath/git/ref/tags/PACK"
)
_GITEE_PACK_API = (
    "https://gitee.com/api/v5/repos/Mark42IRPC/"
    "Aemeath-AIdeskpet/releases/tags/%E6%9C%80%E6%96%B0%E5%8C%85"
)
_GITEE_PACK_PAGE = (
    "https://gitee.com/Mark42IRPC/Aemeath-AIdeskpet/"
    "releases/tag/%E6%9C%80%E6%96%B0%E5%8C%85"
)
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "FlyingSnowVelvet-Updater/1.0",
}
_PAGE_JSON_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "FlyingSnowVelvet-Updater/1.0",
}
_DOWNLOAD_HEADERS = {
    "Accept": "*/*",
    "User-Agent": "FlyingSnowVelvet-Updater/1.0",
}
_VOICE_UPDATE_FORMAT = OFFLINE_UPDATE_FORMAT
_VOICE_UPDATE_PATH = OFFLINE_UPDATE_METADATA_PATH
_HUGGINGFACE_VOICE_UPDATE_URL = (
    f"https://huggingface.co/{VOICE_PACKAGE_HUGGINGFACE_REPO}/resolve/main/"
    f"{_VOICE_UPDATE_PATH}"
)
_MODELSCOPE_VOICE_UPDATE_URL = (
    f"https://www.modelscope.cn/models/{VOICE_PACKAGE_MODELSCOPE_REPO}/resolve/master/"
    f"{_VOICE_UPDATE_PATH}"
)
_HUGGINGFACE_VOICE_FILE_BASE = (
    f"https://huggingface.co/{VOICE_PACKAGE_HUGGINGFACE_REPO}/resolve/main/"
)
_MODELSCOPE_VOICE_FILE_BASE = (
    f"https://www.modelscope.cn/models/{VOICE_PACKAGE_MODELSCOPE_REPO}/resolve/master/"
)

InfoCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, str], None]


class UpdateError(RuntimeError):
    """更新流程异常。"""


def _is_retryable_request_error(exc: requests.RequestException) -> bool:
    response = getattr(exc, "response", None)
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code:
        return status_code == 429 or status_code >= 500
    return True


@dataclass(frozen=True)
class InstalledState:
    version: str
    installed_at: datetime
    revision: str = ""
    source: str = ""


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    published_at: datetime
    asset_name: str
    download_url: str
    source: str = ""
    revision: str = ""
    response_seconds: float = 0.0
    fallback_download_urls: tuple[str, ...] = ()
    archive_sha256: str = ""


@dataclass(frozen=True)
class ReleaseCheckResult:
    installed_state: InstalledState
    release_info: ReleaseInfo
    update_available: bool
    reason: str = ""


@dataclass(frozen=True)
class UpdateResult:
    updated: bool
    installed_state: InstalledState
    release_info: ReleaseInfo
    reason: str = ""
    archive_path: Path | None = None


@dataclass(frozen=True)
class GitSyncSnapshot:
    branch: str
    remote_name: str
    remote_ref: str
    local_commit: str
    local_committed_at: datetime
    remote_commit: str
    remote_committed_at: datetime
    changed_files: tuple[str, ...]
    dirty_files: tuple[str, ...]


@dataclass(frozen=True)
class GitSyncCheckResult:
    snapshot: GitSyncSnapshot
    update_available: bool
    reason: str = ""


@dataclass(frozen=True)
class GitSyncResult:
    updated: bool
    snapshot: GitSyncSnapshot
    reason: str = ""


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    value = value.strip()
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt


def _isoformat(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    text = dt.isoformat()
    return text.replace("+00:00", "Z")


def _select_installer_asset(assets: object, tag: str = "") -> dict | None:
    """Select only a signed native offline installer asset.

    Source archives and the retired ``-green.zip`` package are deliberately
    rejected.  Falling back to a provider's ``zipball_url`` would silently
    turn an update into a source checkout, so callers must fail closed when no
    EXE is attached.
    """
    if not isinstance(assets, list):
        return None
    candidates: list[dict] = []
    exact_name = f"flying snow velvet-{str(tag or '').strip()}-offline-installer.exe".replace(" ", "").lower()
    pattern = re.compile(r"^flyingsnowvelvet-(?:.+-)?offline-installer\.exe$", re.IGNORECASE)
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        lower_name = name.lower().replace(" ", "")
        if pattern.match(lower_name) and url:
            candidates.append(asset)
    if not candidates:
        return None
    for asset in candidates:
        if str(asset.get("name") or "").strip().lower().replace(" ", "") == exact_name:
            return asset
    return sorted(candidates, key=lambda item: str(item.get("name") or "").lower())[0]


# Backward-compatible alias for integrations that imported the old private
# selector.  It intentionally never returns ZIP assets anymore.
_select_zip_asset = _select_installer_asset


def _extract_gitee_attachments(page_data: object) -> list[dict]:
    if not isinstance(page_data, dict):
        return []
    release_root = page_data.get("release")
    release_data = release_root.get("release") if isinstance(release_root, dict) else None
    attached = release_data.get("attach_files") if isinstance(release_data, dict) else None
    if not isinstance(attached, list):
        return []
    normalized: list[dict] = []
    for item in attached:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("file_name") or "").strip()
        raw_url = str(
            item.get("browser_download_url")
            or item.get("download_url")
            or item.get("url")
            or item.get("path")
            or ""
        ).strip()
        if name and raw_url:
            normalized.append({
                "name": name,
                "browser_download_url": urljoin("https://gitee.com", raw_url),
            })
    return normalized


def _extract_gitee_revision(page_data: object) -> str:
    if not isinstance(page_data, dict):
        return ""
    release_root = page_data.get("release")
    tag_data = release_root.get("tag") if isinstance(release_root, dict) else None
    commit_data = tag_data.get("commit") if isinstance(tag_data, dict) else None
    if not isinstance(commit_data, dict):
        return ""
    return str(commit_data.get("id") or "").strip()


def _select_release_source(releases: list[ReleaseInfo]) -> ReleaseInfo:
    if not releases:
        raise UpdateError("没有可用的更新源")
    return max(
        releases,
        key=lambda item: (
            item.published_at,
            -max(0.0, float(item.response_seconds)),
        ),
    )


def _parse_voice_package_release(
    data: object,
    *,
    source_name: str,
    file_base_url: str,
    response_seconds: float,
) -> ReleaseInfo:
    if not isinstance(data, dict) or data.get("format") != _VOICE_UPDATE_FORMAT:
        raise UpdateError(f"{source_name} 更新清单格式无效")
    version = str(data.get("version") or "").strip()
    asset_name = str(data.get("asset_name") or "").strip()
    asset_path = str(data.get("asset_path") or "").strip().replace("\\", "/")
    digest = str(data.get("sha256") or "").strip().lower()
    published_at = _parse_datetime(str(data.get("published_at") or ""))
    relative = PurePosixPath(asset_path)
    if (
        not re.fullmatch(r"[A-Za-z0-9._+-]+", version)
        or not re.fullmatch(r"FlyingSnowVelvet-[A-Za-z0-9._+-]+-Offline-Installer\.zip", asset_name)
        or relative.is_absolute()
        or ".." in relative.parts
        or ":" in asset_path
        or not asset_path.startswith("updates/")
        or relative.name != asset_name
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or published_at.year < 2000
    ):
        raise UpdateError(f"{source_name} 更新清单缺少有效的安装器 ZIP 或校验信息")
    return ReleaseInfo(
        tag=version,
        published_at=published_at,
        asset_name=asset_name,
        download_url=urljoin(file_base_url, quote(asset_path, safe="/")),
        source=source_name,
        revision=str(data.get("revision") or f"sha256:{digest}").strip(),
        response_seconds=response_seconds,
        archive_sha256=digest,
    )


class _UpdateBase:
    def __init__(
        self,
        *,
        info_callback: InfoCallback | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._info_callback = info_callback
        self._progress_callback = progress_callback
        self._last_info_message = ""
        self._last_progress_emit_ts = 0.0
        self._last_progress_key: tuple[int, int, str] | None = None

    def _info(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        if text == self._last_info_message:
            return
        self._last_info_message = text
        if self._info_callback:
            try:
                self._info_callback(text)
                return
            except Exception:
                _logger.debug("update info callback failed", exc_info=True)
        _logger.info("[Update] %s", text)

    def _progress(self, current: int, total: int, message: str = "") -> None:
        current = int(current)
        total = int(total)
        text = str(message or "").strip()
        now = time.monotonic()
        progress_key = (current, total, text)

        should_emit = False
        if self._last_progress_key != progress_key:
            should_emit = True
        elif total > 0 and current >= total:
            should_emit = True
        elif (now - self._last_progress_emit_ts) >= 0.2:
            should_emit = True

        if not should_emit:
            return

        self._last_progress_emit_ts = now
        self._last_progress_key = progress_key
        if text:
            self._info(text)
        if self._progress_callback:
            try:
                self._progress_callback(current, total, text)
                return
            except Exception:
                _logger.debug("update progress callback failed", exc_info=True)


class UpdateManager(_UpdateBase):
    """负责从固定双源检测、下载并交接分发包。"""

    def __init__(
        self,
        *,
        repo: str = GITHUB_REPO,
        state_path: Path | None = None,
        info_callback: InfoCallback | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        super().__init__(
            info_callback=info_callback,
            progress_callback=progress_callback,
        )
        self._repo = repo
        self._state_path = Path(state_path) if state_path else _STATE_PATH

    def check_for_updates(self) -> ReleaseCheckResult:
        installed = self._load_installed_state()
        release = self._fetch_latest_release()
        revision_changed = bool(release.revision) and release.revision != installed.revision
        update_available = release.published_at > installed.installed_at or (
            release.published_at == installed.installed_at and revision_changed
        )
        reason = "update_available" if update_available else "up_to_date"
        if update_available:
            self._info(
                f"检测到新的桌宠包 {release.asset_name}（{release.published_at.date()}），当前版本为 {installed.version}（{installed.installed_at.date()}）。"
            )
        else:
            self._info(
                f"当前已为最新分发包 {installed.version}（{installed.installed_at.date()}），无需更新。"
            )
        return ReleaseCheckResult(
            installed_state=installed,
            release_info=release,
            update_available=update_available,
            reason=reason,
        )

    def install_release(
        self,
        release: ReleaseInfo,
        *,
        launch_installer: bool = True,
        restart_command: list[str] | None = None,
    ) -> UpdateResult:
        from lib.script.app.update_installer import (
            extract_update_installer_bundle,
            launch_update_installer,
            validate_update_installer,
        )

        self._progress(0, 0, f"开始下载桌宠包 {release.asset_name}...")
        staging_dir = _STAGING_ROOT / uuid.uuid4().hex
        download_name = Path(release.asset_name or "FlyingSnowVelvet-Offline-Installer.zip").name
        if Path(download_name).suffix.casefold() not in {".zip", ".exe"}:
            raise UpdateError("更新源未提供离线安装器 ZIP")
        download_path = staging_dir / download_name
        partial_path = download_path.with_suffix(download_path.suffix + ".part")
        try:
            self._download_release(release, partial_path)
            partial_path.replace(download_path)
            self._progress(0, 0, "下载完成，正在校验更新包...")
            if release.archive_sha256:
                digest = hashlib.sha256()
                with download_path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest().casefold() != release.archive_sha256.casefold():
                    raise UpdateError("更新安装器压缩包 SHA-256 校验失败")
            archive_path = (
                extract_update_installer_bundle(download_path, staging_dir / "installer")
                if download_path.suffix.casefold() == ".zip"
                else download_path
            )
            validate_update_installer(archive_path)
            release_payload = {
                "tag": release.tag,
                "published_at": _isoformat(release.published_at),
                "revision": release.revision,
                "source": release.source,
            }
            if launch_installer:
                launch_update_installer(
                    archive_path,
                    _PROJECT_ROOT,
                    self._state_path,
                    release_payload,
                    restart_command=restart_command,
                )
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            if isinstance(exc, UpdateError):
                raise
            raise UpdateError(f"无法交接更新安装进程：{exc}") from exc

        pending_state = InstalledState(
            release.tag,
            release.published_at,
            release.revision,
            release.source,
        )
        reason = "install_scheduled" if launch_installer else "download_ready"
        message = "离线安装器已启动，桌宠将在退出后完成更新。" if launch_installer else "离线安装器已下载并通过校验，请启动安装器完成更新。"
        self._progress(1, 1, message)
        return UpdateResult(
            True,
            pending_state,
            release,
            reason=reason,
            archive_path=archive_path,
        )

    def launch_pending_update(
        self,
        update: UpdateResult,
        *,
        restart_command: list[str] | None = None,
    ) -> UpdateResult:
        """将已下载的离线 EXE 交给原生安装器。"""
        from lib.script.app.update_installer import launch_update_installer

        archive_path = update.archive_path
        if archive_path is None or not Path(archive_path).is_file():
            raise UpdateError("待安装更新包不存在，请重新下载。")
        release = update.release_info
        release_payload = {
            "tag": release.tag,
            "published_at": _isoformat(release.published_at),
            "revision": release.revision,
            "source": release.source,
        }
        launch_update_installer(
            archive_path,
            _PROJECT_ROOT,
            self._state_path,
            release_payload,
            restart_command=restart_command,
        )
        return replace(update, reason="install_scheduled")

    def check_and_update(self) -> UpdateResult:
        check_result = self.check_for_updates()
        if not check_result.update_available:
            return UpdateResult(
                False,
                check_result.installed_state,
                check_result.release_info,
                reason=check_result.reason,
            )
        return self.install_release(check_result.release_info)

    def _load_installed_state(self) -> InstalledState:
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                version = str(data.get("version") or RESOURCE_VERSION)
                installed_at = _parse_datetime(data.get("installed_at"))
                return InstalledState(
                    version,
                    installed_at,
                    str(data.get("revision") or ""),
                    str(data.get("source") or ""),
                )
            except Exception as exc:
                _logger.warning("failed to parse update state: %s", exc)
        return InstalledState(
            version=RESOURCE_VERSION,
            installed_at=_parse_datetime(RESOURCE_RELEASE_DATE),
        )

    def _fetch_latest_release(self) -> ReleaseInfo:
        fetchers = (self._fetch_huggingface_voice_release, self._fetch_modelscope_voice_release)
        releases: list[ReleaseInfo] = []
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="release-source") as pool:
            futures = [pool.submit(fetcher) for fetcher in fetchers]
            for future in as_completed(futures):
                try:
                    releases.append(future.result())
                except Exception as exc:
                    errors.append(str(exc))
        if not releases:
            detail = "；".join(errors) if errors else "未知网络错误"
            raise UpdateError(f"Hugging Face 和 ModelScope 语音包仓库更新源均不可用：{detail}")
        selected = _select_release_source(releases)
        fallback_urls = tuple(
            item.download_url
            for item in releases
            if item is not selected
            and item.download_url
            and item.revision
            and item.revision == selected.revision
            and item.archive_sha256 == selected.archive_sha256
        )
        if fallback_urls:
            selected = replace(selected, fallback_download_urls=fallback_urls)
        self._info(f"已选择 {selected.source} 更新源（探测 {selected.response_seconds:.2f}s）。")
        return selected

    @staticmethod
    def _fetch_huggingface_voice_release() -> ReleaseInfo:
        started = time.monotonic()
        data = UpdateManager._fetch_release_json(
            _HUGGINGFACE_VOICE_UPDATE_URL,
            "Hugging Face 语音包仓库",
            headers=_PAGE_JSON_HEADERS,
        )
        return _parse_voice_package_release(
            data,
            source_name="Hugging Face",
            file_base_url=_HUGGINGFACE_VOICE_FILE_BASE,
            response_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _fetch_modelscope_voice_release() -> ReleaseInfo:
        started = time.monotonic()
        data = UpdateManager._fetch_release_json(
            _MODELSCOPE_VOICE_UPDATE_URL,
            "ModelScope 语音包仓库",
            headers=_PAGE_JSON_HEADERS,
        )
        return _parse_voice_package_release(
            data,
            source_name="ModelScope",
            file_base_url=_MODELSCOPE_VOICE_FILE_BASE,
            response_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _fetch_github_pack_release() -> ReleaseInfo:
        started = time.monotonic()
        data = UpdateManager._fetch_release_json(_GITHUB_PACK_API, "GitHub PACK")
        if not isinstance(data, dict) or bool(data.get("draft")):
            raise UpdateError("GitHub PACK release 不存在或尚未发布")
        tag = str(data.get("tag_name") or "PACK")
        asset = _select_installer_asset(data.get("assets"), tag)
        download_url = str((asset or {}).get("browser_download_url") or "").strip()
        if not download_url:
            raise UpdateError("GitHub PACK release 缺少离线安装器 EXE")
        asset_name = str((asset or {}).get("name") or "FlyingSnowVelvet-PACK-Offline-Installer.exe")
        updated_at = str(data.get("updated_at") or data.get("published_at") or data.get("created_at") or "")
        ref_data = UpdateManager._fetch_release_json(_GITHUB_PACK_REF_API, "GitHub PACK tag")
        ref_object = ref_data.get("object") if isinstance(ref_data, dict) else None
        revision = str(ref_object.get("sha") or "") if isinstance(ref_object, dict) else ""
        if not revision:
            revision = f"release:{data.get('id', '')}:{updated_at}"
        return ReleaseInfo(
            tag=tag,
            published_at=_parse_datetime(updated_at),
            asset_name=asset_name,
            download_url=download_url,
            source="GitHub",
            revision=revision,
            response_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _fetch_gitee_pack_release() -> ReleaseInfo:
        started = time.monotonic()
        data = UpdateManager._fetch_release_json(_GITEE_PACK_API, "Gitee 最新包")
        if not isinstance(data, dict) or bool(data.get("prerelease")):
            raise UpdateError("Gitee 最新包 release 不存在或尚未发布")
        tag = str(data.get("tag_name") or "最新包")
        attachments: list[dict] = []
        page_revision = ""
        try:
            page_data = UpdateManager._fetch_release_json(
                _GITEE_PACK_PAGE,
                "Gitee 最新包页面",
                headers=_PAGE_JSON_HEADERS,
            )
            attachments = _extract_gitee_attachments(page_data)
            page_revision = _extract_gitee_revision(page_data)
        except UpdateError:
            pass
        api_assets = data.get("assets") if isinstance(data.get("assets"), list) else []
        asset = _select_installer_asset([*attachments, *api_assets], tag)
        if asset is None:
            raise UpdateError("Gitee 最新包 release 缺少离线安装器 EXE")
        published_text = str(data.get("updated_at") or data.get("created_at") or "")
        revision = page_revision or str(data.get("target_commitish") or "")
        download_url = str(asset.get("browser_download_url") or "")
        return ReleaseInfo(
            tag=tag,
            published_at=_parse_datetime(published_text),
            asset_name=str(asset.get("name") or "FlyingSnowVelvet-latest-Offline-Installer.exe"),
            download_url=download_url,
            source="Gitee",
            revision=revision or f"release:{data.get('id', '')}:{published_text}",
            response_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _fetch_release_json(
        url: str,
        source_name: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> object:
        last_error: requests.RequestException | None = None
        for attempt in range(1, 4):
            try:
                response = requests.get(
                    url,
                    timeout=10,
                    headers=headers or _API_HEADERS,
                )
                response.raise_for_status()
                return response.json()
            except ValueError as exc:
                raise UpdateError(f"{source_name} 返回格式异常") from exc
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= 3 or not _is_retryable_request_error(exc):
                    break
                time.sleep(0.25 * attempt)
        raise UpdateError(f"{source_name} 读取失败：{last_error}") from last_error

    def _download_release(self, release: ReleaseInfo, dest_path: Path) -> None:
        urls = (release.download_url, *release.fallback_download_urls)
        errors: list[str] = []
        for index, download_url in enumerate(urls):
            try:
                self._download_url(download_url, dest_path)
                return
            except UpdateError as exc:
                errors.append(str(exc))
                if index + 1 < len(urls):
                    self._info("当前镜像下载失败，正在切换同 revision 备用源...")
        raise UpdateError("；".join(errors) or "没有可用的更新包下载地址")

    def _download_url(self, download_url: str, dest_path: Path) -> None:
        last_error: requests.RequestException | None = None
        for attempt in range(1, 4):
            try:
                with requests.get(
                    download_url,
                    timeout=(10, 60),
                    stream=True,
                    headers=_DOWNLOAD_HEADERS,
                ) as resp:
                    resp.raise_for_status()
                    total_text = str(resp.headers.get("Content-Length") or "").strip()
                    total_bytes = int(total_text) if total_text.isdigit() else 0
                    downloaded = 0
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(dest_path, "wb") as fp:
                        for chunk in resp.iter_content(chunk_size=512 * 1024):
                            if not chunk:
                                continue
                            fp.write(chunk)
                            downloaded += len(chunk)
                            self._progress(
                                downloaded,
                                total_bytes,
                                f"正在下载离线安装器… {downloaded // (1024 * 1024)} MB",
                            )
                    if total_bytes and downloaded != total_bytes:
                        raise UpdateError(
                            f"下载内容长度不完整：{downloaded}/{total_bytes} bytes"
                        )
                return
            except requests.RequestException as exc:
                last_error = exc
                dest_path.unlink(missing_ok=True)
                if attempt >= 3 or not _is_retryable_request_error(exc):
                    break
                self._info(f"下载连接中断，正在进行第 {attempt + 1} 次尝试...")
                time.sleep(0.5 * attempt)
        raise UpdateError(f"下载更新包失败：{last_error}") from last_error


class GitSyncManager(_UpdateBase):
    """负责检查并同步当前仓库的开发版代码。"""

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        remote_name: str | None = None,
        branch: str | None = None,
        info_callback: InfoCallback | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        super().__init__(
            info_callback=info_callback,
            progress_callback=progress_callback,
        )
        self._project_root = Path(project_root) if project_root else _PROJECT_ROOT
        self._remote_name = str(remote_name or "").strip() or None
        self._branch = str(branch or "").strip() or None

    def check_for_updates(self) -> GitSyncCheckResult:
        self._ensure_git_repo()
        self._progress(0, 0, "正在通过 Git 检查开发版最新改动...")
        snapshot = self._build_snapshot(fetch_remote=True)
        remote_is_newer = snapshot.remote_committed_at > snapshot.local_committed_at
        same_time_but_new_commit = (
            snapshot.remote_committed_at == snapshot.local_committed_at
            and snapshot.remote_commit != snapshot.local_commit
        )
        update_available = bool(snapshot.changed_files) and (
            remote_is_newer or same_time_but_new_commit
        )
        if update_available:
            reason = "update_available"
        elif snapshot.remote_commit == snapshot.local_commit:
            reason = "up_to_date"
        elif snapshot.local_committed_at > snapshot.remote_committed_at:
            reason = "local_ahead"
        else:
            reason = "up_to_date"
        if update_available:
            self._info(
                f"检测到开发版新提交（{snapshot.remote_committed_at.date()}），共 {len(snapshot.changed_files)} 个差异文件。"
            )
        elif reason == "local_ahead":
            self._info(
                f"当前本地提交时间更新（{snapshot.local_committed_at.date()}），无需回退到远端开发版。"
            )
        else:
            self._info(
                f"当前开发版已同步到最新提交（{snapshot.local_committed_at.date()}）。"
            )
        return GitSyncCheckResult(
            snapshot=snapshot,
            update_available=update_available,
            reason=reason,
        )

    def sync_to_remote(self, snapshot: GitSyncSnapshot | None = None) -> GitSyncResult:
        self._ensure_git_repo()
        current = snapshot if snapshot is not None else self._build_snapshot(fetch_remote=True)
        if current.dirty_files:
            dirty_preview = "、".join(current.dirty_files[:4])
            if len(current.dirty_files) > 4:
                dirty_preview += " 等"
            raise UpdateError(
                f"检测到本地未提交改动，为避免误覆盖，暂不自动同步：{dirty_preview}"
            )
        if not current.changed_files:
            self._progress(1, 1, "当前开发版没有需要同步的差异文件。")
            return GitSyncResult(False, current, reason="up_to_date")

        self._progress(0, 4, "正在刷新远端开发版提交信息...")
        refreshed = self._build_snapshot(fetch_remote=True)
        self._progress(1, 4, "正在确认差异文件列表...")
        if not refreshed.changed_files:
            self._progress(4, 4, "当前开发版没有需要同步的差异文件。")
            return GitSyncResult(False, refreshed, reason="up_to_date")

        self._progress(
            2,
            4,
            f"准备同步 {len(refreshed.changed_files)} 个差异文件...",
        )
        self._run_git("reset", "--hard", refreshed.remote_ref)
        self._progress(3, 4, "Git 覆盖完成，正在重新读取本地提交状态...")
        final_snapshot = self._build_snapshot(fetch_remote=False)
        self._progress(4, 4, "开发版同步完成。")
        return GitSyncResult(True, final_snapshot, reason="updated")

    def _build_snapshot(self, *, fetch_remote: bool) -> GitSyncSnapshot:
        remote_name, branch = self._resolve_remote_and_branch()
        remote_ref = f"{remote_name}/{branch}"
        if fetch_remote:
            self._run_git("fetch", remote_name, branch)
        local_commit = self._run_git("rev-parse", "HEAD").strip()
        remote_commit = self._run_git("rev-parse", remote_ref).strip()
        local_committed_at = _parse_datetime(
            self._run_git("log", "-1", "--format=%cI", "HEAD").strip()
        )
        remote_committed_at = _parse_datetime(
            self._run_git("log", "-1", "--format=%cI", remote_ref).strip()
        )
        changed_files = tuple(
            line.strip()
            for line in self._run_git(
                "diff",
                "--name-only",
                "--diff-filter=ACDMRT",
                "HEAD",
                remote_ref,
            ).splitlines()
            if line.strip()
        )
        dirty_files = self._list_dirty_files()
        return GitSyncSnapshot(
            branch=branch,
            remote_name=remote_name,
            remote_ref=remote_ref,
            local_commit=local_commit,
            local_committed_at=local_committed_at,
            remote_commit=remote_commit,
            remote_committed_at=remote_committed_at,
            changed_files=changed_files,
            dirty_files=dirty_files,
        )

    def _resolve_remote_and_branch(self) -> tuple[str, str]:
        upstream = self._run_git_optional(
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{u}",
        )
        if upstream and "/" in upstream:
            remote_name, branch = upstream.split("/", 1)
            return remote_name, branch

        branch = self._branch or self._run_git("branch", "--show-current").strip()
        if not branch:
            raise UpdateError("无法解析当前 Git 分支")
        remote_name = self._remote_name or "origin"
        return remote_name, branch

    def _list_dirty_files(self) -> tuple[str, ...]:
        output = self._run_git(
            "status",
            "--porcelain",
            "--untracked-files=no",
        )
        dirty_paths: list[str] = []
        for line in output.splitlines():
            entry = line[3:].strip() if len(line) > 3 else line.strip()
            if entry:
                dirty_paths.append(entry)
        return tuple(dirty_paths)

    def _ensure_git_repo(self) -> None:
        inside = self._run_git_optional("rev-parse", "--is-inside-work-tree")
        if str(inside).strip().lower() != "true":
            raise UpdateError("当前目录不是 Git 仓库，无法同步开发版。")

    def _run_git_optional(self, *args: str) -> str:
        try:
            return self._run_git(*args)
        except UpdateError:
            return ""

    def _run_git(self, *args: str) -> str:
        cmd = ["git", *args]
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(self._project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        except FileNotFoundError as exc:
            raise UpdateError("未检测到 git，请先安装并加入 PATH。") from exc
        except subprocess.TimeoutExpired as exc:
            raise UpdateError(f"Git 命令超时：{' '.join(cmd)}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            detail = detail or "未知错误"
            raise UpdateError(f"Git 命令失败：{' '.join(cmd)}\n{detail}")
        return completed.stdout.strip()
