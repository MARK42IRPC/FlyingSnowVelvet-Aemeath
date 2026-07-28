"""GitHub 分发更新与开发版 Git 同步管理器。"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

import requests

from config.version_info import (
    GITHUB_REPO,
    RESOURCE_RELEASE_DATE,
    RESOURCE_VERSION,
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


def _select_zip_asset(assets: object, tag: str = "") -> dict | None:
    if not isinstance(assets, list):
        return None
    candidates: list[dict] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        lower_name = name.lower()
        if lower_name.endswith(".zip") and not lower_name.endswith("-green.zip") and url:
            candidates.append(asset)
    if not candidates:
        return None
    generated_name = f"{str(tag or '').strip()}.zip".lower()
    for asset in candidates:
        if str(asset.get("name") or "").strip().lower() != generated_name:
            return asset
    return candidates[0]


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
                f"检测到新的分发包 {release.tag}（{release.published_at.date()}），当前版本为 {installed.version}（{installed.installed_at.date()}）。"
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

    def install_release(self, release: ReleaseInfo) -> UpdateResult:
        from lib.script.app.update_installer import (
            launch_update_installer,
            validate_update_archive,
        )

        self._progress(0, 0, f"开始下载分发包 {release.tag}...")
        staging_dir = _STAGING_ROOT / uuid.uuid4().hex
        archive_name = Path(release.asset_name or "release.zip").name
        if not archive_name.lower().endswith(".zip"):
            archive_name += ".zip"
        archive_path = staging_dir / archive_name
        partial_path = archive_path.with_suffix(archive_path.suffix + ".part")
        try:
            self._download_release(release, partial_path)
            partial_path.replace(archive_path)
            self._progress(0, 0, "下载完成，正在校验更新包...")
            validate_update_archive(archive_path)
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
        self._progress(1, 1, "更新包已就绪，退出后将自动覆盖安装并重启。")
        return UpdateResult(True, pending_state, release, reason="install_scheduled")

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
        fetchers = (self._fetch_github_pack_release, self._fetch_gitee_pack_release)
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
            raise UpdateError(f"GitHub 和 Gitee 更新源均不可用：{detail}")
        selected = _select_release_source(releases)
        fallback_urls = tuple(
            item.download_url
            for item in releases
            if item is not selected
            and item.download_url
            and item.revision
            and item.revision == selected.revision
        )
        if fallback_urls:
            selected = replace(selected, fallback_download_urls=fallback_urls)
        self._info(f"已选择 {selected.source} 更新源（探测 {selected.response_seconds:.2f}s）。")
        return selected

    @staticmethod
    def _fetch_github_pack_release() -> ReleaseInfo:
        started = time.monotonic()
        data = UpdateManager._fetch_release_json(_GITHUB_PACK_API, "GitHub PACK")
        if not isinstance(data, dict) or bool(data.get("draft")):
            raise UpdateError("GitHub PACK release 不存在或尚未发布")
        tag = str(data.get("tag_name") or "PACK")
        asset = _select_zip_asset(data.get("assets"), tag)
        download_url = str(
            (asset or {}).get("browser_download_url") or data.get("zipball_url") or ""
        ).strip()
        if not download_url:
            raise UpdateError("GitHub PACK release 缺少 ZIP 下载地址")
        asset_name = str((asset or {}).get("name") or "FlyingSnowVelvet-PACK.zip")
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
        asset = _select_zip_asset([*attachments, *api_assets], tag)
        if asset is None:
            raise UpdateError("Gitee 最新包 release 缺少 ZIP 下载地址")
        published_text = str(data.get("updated_at") or data.get("created_at") or "")
        revision = page_revision or str(data.get("target_commitish") or "")
        download_url = str(asset.get("browser_download_url") or "")
        return ReleaseInfo(
            tag=tag,
            published_at=_parse_datetime(published_text),
            asset_name=str(asset.get("name") or "Aemeath-latest.zip"),
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
                                "正在下载新的分发包...",
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
