"""GitHub 分发更新与开发版 Git 同步管理器。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

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
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "FlyingSnowVelvet-Updater/1.0",
}
_ASSET_HEADERS = {
    "Accept": "application/octet-stream",
    "User-Agent": "FlyingSnowVelvet-Updater/1.0",
}
_PROTECTED_ROOTS = ("logs", "resc/user", "resc/models")
_PROTECTED_FILES = ("py.ini",)

InfoCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, str], None]


class UpdateError(RuntimeError):
    """更新流程异常。"""


@dataclass(frozen=True)
class InstalledState:
    version: str
    installed_at: datetime


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    published_at: datetime
    asset_name: str
    download_url: str


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


def _normalize_relative_path(rel_path: Path) -> str:
    if rel_path == Path("."):
        return ""
    parts = [part for part in rel_path.parts if part not in (".", "")]
    return "/".join(parts)


def _is_protected_path(rel_path: Path) -> bool:
    rel = _normalize_relative_path(rel_path)
    if not rel:
        return False
    for file_name in _PROTECTED_FILES:
        if rel == file_name:
            return True
    for root in _PROTECTED_ROOTS:
        if rel == root or rel.startswith(root + "/"):
            return True
    return False


class _UpdateBase:
    def __init__(
        self,
        *,
        info_callback: InfoCallback | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._info_callback = info_callback
        self._progress_callback = progress_callback

    def _info(self, message: str) -> None:
        if self._info_callback:
            try:
                self._info_callback(message)
                return
            except Exception:
                _logger.debug("update info callback failed", exc_info=True)
        _logger.info("[Update] %s", message)

    def _progress(self, current: int, total: int, message: str = "") -> None:
        if message:
            self._info(message)
        if self._progress_callback:
            try:
                self._progress_callback(int(current), int(total), message)
                return
            except Exception:
                _logger.debug("update progress callback failed", exc_info=True)


class UpdateManager(_UpdateBase):
    """负责检测 GitHub 发布并自动更新本地分发包。"""

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
        update_available = release.published_at > installed.installed_at
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
        self._progress(0, 0, f"开始下载分发包 {release.tag}...")
        with tempfile.TemporaryDirectory(prefix="fs-update-") as tmp_dir:
            tmp_path = Path(tmp_dir) / (release.asset_name or "release.zip")
            self._download_release(release, tmp_path)
            self._progress(0, 0, "下载完成，正在解压并覆盖文件...")
            self._extract_and_copy(tmp_path)

        self._write_installed_state(release)
        new_state = InstalledState(release.tag, release.published_at)
        self._progress(1, 1, "分发包更新完成，建议重启程序以载入最新内容。")
        return UpdateResult(True, new_state, release, reason="updated")

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
                return InstalledState(version, installed_at)
            except Exception as exc:
                _logger.warning("failed to parse update state: %s", exc)
        return InstalledState(
            version=RESOURCE_VERSION,
            installed_at=_parse_datetime(RESOURCE_RELEASE_DATE),
        )

    def _write_installed_state(self, release: ReleaseInfo) -> None:
        payload = {
            "version": release.tag,
            "installed_at": _isoformat(release.published_at),
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _fetch_latest_release(self) -> ReleaseInfo:
        url = f"https://api.github.com/repos/{self._repo}/releases/latest"
        try:
            resp = requests.get(url, timeout=15, headers=_API_HEADERS)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise UpdateError(f"无法访问 GitHub：{exc}") from exc
        except ValueError as exc:
            raise UpdateError("GitHub 返回格式异常") from exc

        tag = str(data.get("tag_name") or data.get("name") or "latest").strip()
        published = _parse_datetime(data.get("published_at") or data.get("created_at"))
        assets = data.get("assets") or []
        asset_entry = next(
            (
                asset
                for asset in assets
                if str(asset.get("name") or "").lower().endswith(".zip")
            ),
            None,
        )
        download_url = ""
        asset_name = ""
        if asset_entry:
            download_url = str(asset_entry.get("browser_download_url") or "").strip()
            asset_name = str(asset_entry.get("name") or "").strip()
        if not download_url:
            download_url = str(data.get("zipball_url") or "").strip()
            asset_name = asset_name or f"{tag or 'latest'}.zip"
        if not download_url:
            raise UpdateError("GitHub 发布缺少可下载的 zip 资源")

        return ReleaseInfo(
            tag=tag or "latest",
            published_at=published,
            asset_name=asset_name or "release.zip",
            download_url=download_url,
        )

    def _download_release(self, release: ReleaseInfo, dest_path: Path) -> None:
        try:
            with requests.get(
                release.download_url,
                timeout=60,
                stream=True,
                headers=_ASSET_HEADERS,
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
        except requests.RequestException as exc:
            raise UpdateError(f"下载更新包失败：{exc}") from exc

    def _extract_and_copy(self, archive_path: Path) -> None:
        if not archive_path.exists():
            raise UpdateError("更新包不存在或已被清理")
        with tempfile.TemporaryDirectory(prefix="fs-update-extract-") as extract_dir:
            try:
                with zipfile.ZipFile(archive_path, "r") as zf:
                    zf.extractall(extract_dir)
            except zipfile.BadZipFile as exc:
                raise UpdateError(f"更新包损坏：{exc}") from exc
            content_root = self._resolve_content_root(Path(extract_dir))
            copy_ops = self._collect_copy_operations(content_root)
            total_ops = len(copy_ops)
            for index, (src_file, dest_file, rel_text) in enumerate(copy_ops, start=1):
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                self._progress(
                    index,
                    total_ops,
                    f"正在覆盖文件：{rel_text}",
                )

    @staticmethod
    def _resolve_content_root(extracted_root: Path) -> Path:
        markers = ("install_deps.py", "README.md", "lib")
        if any((extracted_root / marker).exists() for marker in markers):
            return extracted_root
        children = [
            child for child in extracted_root.iterdir() if child.name != "__MACOSX"
        ]
        if len(children) == 1 and children[0].is_dir():
            return children[0]
        return extracted_root

    def _collect_copy_operations(
        self,
        source_root: Path,
    ) -> list[tuple[Path, Path, str]]:
        operations: list[tuple[Path, Path, str]] = []
        for root, dirs, files in os.walk(source_root):
            rel_dir = Path(root).relative_to(source_root)
            if rel_dir != Path(".") and _is_protected_path(rel_dir):
                dirs[:] = []
                continue
            target_dir = (
                _PROJECT_ROOT if rel_dir == Path(".") else _PROJECT_ROOT / rel_dir
            )
            for file_name in files:
                rel_file = (rel_dir / file_name) if rel_dir != Path(".") else Path(file_name)
                if _is_protected_path(rel_file):
                    continue
                src_file = Path(root) / file_name
                dest_file = target_dir / file_name
                operations.append(
                    (src_file, dest_file, _normalize_relative_path(rel_file) or file_name)
                )
        return operations


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
