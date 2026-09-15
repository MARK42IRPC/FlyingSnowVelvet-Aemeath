"""Hugging Face / ModelScope 分发更新与开发版 Git 同步管理器。"""

from __future__ import annotations

import json
import hashlib
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
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
# GitHub 与 Gitee 的 release 对单文件有体积上限，装不下离线安装器 ZIP，因此不再作为
# 更新源：只探测 Hugging Face 与 ModelScope，与原生在线安装器的两个镜像保持一致。
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

# Every update source is probed at the same time and the whole probe has a
# hard ceiling.  The native online installer uses the same idea with bounded
# 2s/4s/8s/16s rounds, so a stalled mirror can never freeze the check.  Once a
# source answers we keep listening for a couple of seconds to learn about a
# faster mirror, then return the best answer we have.
_PROBE_BUDGET_SECONDS = 16.0
_PROBE_GRACE_SECONDS = 2.0

InfoCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, str], None]


class UpdateError(RuntimeError):
    """更新流程异常。"""


def _restart_workbench_window() -> None:
    """按原页面重新打开被更新流程关掉的控制面板窗口。"""
    try:
        from lib.script.app.workbench_helper import relaunch_workbench_helper

        relaunch_workbench_helper()
    except Exception as exc:
        _logger.debug("重新打开控制面板窗口失败: %s", exc)


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
    kind: str = "installer"


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
    # 需要告诉用户的补充说明：被占用而登记为下次启动补装的文件、结束掉的后台进程等。
    notes: tuple[str, ...] = ()


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


def _order_sources_by_speed(releases: list[ReleaseInfo]) -> list[ReleaseInfo]:
    """Order mirror candidates by measured manifest response latency."""
    return sorted(releases, key=lambda item: max(0.0, float(item.response_seconds)))


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
    kind = "resources" if asset_name.endswith("-Resources.zip") else "installer"
    if (
        not re.fullmatch(r"[A-Za-z0-9._+-]+", version)
        or not re.fullmatch(r"FlyingSnowVelvet-[A-Za-z0-9._+-]+-(?:Offline-Installer|Resources)\.zip", asset_name)
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
        kind=kind,
    )


def _probe_source(fetcher: Callable[..., ReleaseInfo], deadline: float) -> ReleaseInfo:
    """Run one source probe under the shared probe deadline."""
    return fetcher(deadline=deadline)


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
        # 远端发布时间必须严格晚于本机已安装的时间戳：本地更晚说明本机（开发版，
        # 或刚覆盖过资源包）已经不比远端旧，此时再提示就是在引导一次降级；相等说明
        # 同一个包已经装过（资源包覆盖后本地时间戳直接取远端发布时间），也必须归到
        # “已经是最新包”。revision 只用于识别同一版本的重发，且两侧都带 revision 时
        # 才比较，避免缺失的旧状态文件变成一个永久更新提示。
        same_version = release.tag == installed.version
        newer_release = release.published_at > installed.installed_at
        revision_changed = (
            bool(release.revision)
            and bool(installed.revision)
            and release.revision != installed.revision
        )
        update_available = newer_release and (not same_version or revision_changed)
        reason = "update_available" if update_available else "up_to_date"
        if update_available:
            self._info(
                f"检测到新的分发包 {release.tag}（{release.published_at.date()}），当前版本 {installed.version}（{installed.installed_at.date()}）。"
            )
        else:
            self._info(
                f"当前已是最新分发包 {installed.version}（{installed.installed_at.date()}），无需更新。"
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
            defer_overlay_leftovers,
            extract_update_installer_bundle,
            install_resource_bundle,
            launch_update_installer,
            validate_update_installer,
        )
        from lib.script.app.update_locks import release_install_directory_locks

        self._progress(0, 0, f"开始下载分发包 {release.tag}（{release.asset_name}）...")
        staging_dir = _STAGING_ROOT / uuid.uuid4().hex
        download_name = Path(release.asset_name or "FlyingSnowVelvet-Offline-Installer.zip").name
        if Path(download_name).suffix.casefold() not in {".zip", ".exe"}:
            raise UpdateError("更新源未提供离线安装器 ZIP")
        download_path = staging_dir / download_name
        partial_path = download_path.with_suffix(download_path.suffix + ".part")
        notes: list[str] = []
        try:
            # 下载时顺手算出 SHA-256：清单里的哈希就是这份文件唯一要核对的校验值，
            # 不再在下载结束后把几百兆重新读一遍（那正是“校验更新包”卡住的原因）。
            digest = self._download_release(release, partial_path)
            partial_path.replace(download_path)
            self._progress(0, 0, "正在核对更新包 SHA-256…")
            if release.archive_sha256 and not digest:
                raise UpdateError("更新包缺少可校验的 SHA-256，已取消更新")
            if release.archive_sha256 and digest.casefold() != release.archive_sha256.casefold():
                raise UpdateError("更新安装器压缩包 SHA-256 校验失败")
            if release.kind == "resources":
                archive_path = download_path
                # Resource overlays are applied while the app is running; no
                # native installer/EXE is downloaded or executed.
                released = release_install_directory_locks(
                    _PROJECT_ROOT, info=self._info
                )
                if released.processes:
                    notes.append(
                        f"已结束 {len(released.processes)} 个占用安装目录的后台进程。"
                    )
                outcome = install_resource_bundle(
                    archive_path,
                    _PROJECT_ROOT,
                    progress=lambda text: self._progress(0, 0, text),
                )
                if outcome.locked:
                    deferred = defer_overlay_leftovers(
                        outcome,
                        outcome.target_root or _PROJECT_ROOT,
                        release={
                            "tag": release.tag,
                            "published_at": _isoformat(release.published_at),
                            "revision": release.revision,
                            "source": release.source,
                        },
                    )
                    if deferred:
                        preview = "、".join(deferred[:3])
                        more = "" if len(deferred) <= 3 else f" 等 {len(deferred)} 个文件"
                        notes.append(
                            f"{len(deferred)} 个文件正在使用中，已登记为下次启动时替换：{preview}{more}"
                        )
                if released.workbench_helper_pid is not None:
                    # 覆盖安装做完再开窗：提前打开会把刚腾出来的文件重新锁上。
                    _restart_workbench_window()
                self._save_installed_state(
                    InstalledState(
                        release.tag,
                        release.published_at,
                        release.revision,
                        release.source,
                    )
                )
            else:
                # 下载文件就是清单里的那一份，SHA-256 已经覆盖了安装器内置归档的全部
                # 字节，这里只做 PE 尾记录与 ZIP 目录的结构检查。
                archive_path = (
                    extract_update_installer_bundle(download_path, staging_dir / "installer")
                    if download_path.suffix.casefold() == ".zip"
                    else download_path
                )
                validate_update_installer(
                    archive_path, verify_payload=not release.archive_sha256
                )
            release_payload = {
                "tag": release.tag,
                "published_at": _isoformat(release.published_at),
                "revision": release.revision,
                "source": release.source,
            }
            if launch_installer and release.kind != "resources":
                # 这里不重开控制面板：桌宠马上要退出，任何还活着的自有进程都会
                # 挡住原生安装器切换安装目录。
                released = release_install_directory_locks(
                    _PROJECT_ROOT, info=self._info
                )
                if released.processes:
                    notes.append(
                        f"已结束 {len(released.processes)} 个占用安装目录的后台进程。"
                    )
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
        reason = "resources_installed" if release.kind == "resources" else ("install_scheduled" if launch_installer else "download_ready")
        message = "离线安装器已启动，桌宠将在退出后完成更新。" if launch_installer else "离线安装器已下载并通过校验，请启动安装器完成更新。"
        if release.kind == "resources":
            message = "资源包已安装，后续启动将使用最新资源。"
        self._progress(1, 1, message)
        return UpdateResult(
            True,
            pending_state,
            release,
            reason=reason,
            archive_path=archive_path,
            notes=tuple(notes),
        )

    def launch_pending_update(
        self,
        update: UpdateResult,
        *,
        restart_command: list[str] | None = None,
    ) -> UpdateResult:
        """将已下载的离线 EXE 交给原生安装器。"""
        from lib.script.app.update_installer import launch_update_installer
        from lib.script.app.update_locks import release_install_directory_locks

        archive_path = update.archive_path
        if archive_path is None or not Path(archive_path).is_file():
            raise UpdateError("待安装更新包不存在，请重新下载。")
        release = update.release_info
        if release.kind == "resources":
            raise UpdateError("资源包已经安装，无需启动离线安装器。")
        release_payload = {
            "tag": release.tag,
            "published_at": _isoformat(release.published_at),
            "revision": release.revision,
            "source": release.source,
        }
        # 桌宠退出的那一刻，办公侧车、控制面板 helper 与语音 worker 还在用安装目录里的
        # node.exe / DLL；先把它们结束，原生安装器接管目录切换时就不会再撞占用。
        released = release_install_directory_locks(_PROJECT_ROOT, info=self._info)
        launch_update_installer(
            archive_path,
            _PROJECT_ROOT,
            self._state_path,
            release_payload,
            restart_command=restart_command,
        )
        notes = update.notes
        if released.processes:
            notes = (
                *notes,
                f"已结束 {len(released.processes)} 个占用安装目录的后台进程。",
            )
        return replace(update, reason="install_scheduled", notes=notes)

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

    def _save_installed_state(self, state: InstalledState) -> None:
        """Persist the resource revision after an in-process overlay."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(
                {
                    "version": state.version,
                    "installed_at": _isoformat(state.installed_at),
                    "revision": state.revision,
                    "source": state.source,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _fetch_latest_release(self) -> ReleaseInfo:
        # 只探测两个模型仓库：它们是仅有的能放下离线安装器 ZIP 的地方，也正是原生
        # 在线安装器依次尝试的两个镜像。探测一个永远给不出安装包的源只会白等超时。
        fetchers = (
            self._fetch_huggingface_voice_release,
            self._fetch_modelscope_voice_release,
        )
        deadline = time.monotonic() + _PROBE_BUDGET_SECONDS
        releases: list[ReleaseInfo] = []
        errors: list[str] = []
        first_success = 0.0
        pool = ThreadPoolExecutor(max_workers=len(fetchers), thread_name_prefix="release-source")
        try:
            pending = {pool.submit(_probe_source, fetcher, deadline) for fetcher in fetchers}
            while pending:
                now = time.monotonic()
                if now >= deadline:
                    break
                wait_for = deadline - now
                if first_success:
                    wait_for = min(wait_for, max(0.0, first_success + _PROBE_GRACE_SECONDS - now))
                if wait_for <= 0:
                    break
                done, pending = wait(pending, timeout=wait_for)
                for future in done:
                    try:
                        releases.append(future.result())
                        if not first_success:
                            first_success = time.monotonic()
                    except Exception as exc:
                        errors.append(str(exc))
                if first_success and time.monotonic() >= first_success + _PROBE_GRACE_SECONDS:
                    break
        finally:
            # A stalled mirror must never hold the check open past the budget.
            pool.shutdown(wait=False)
        if not releases:
            detail = "；".join(errors) if errors else "未知网络错误"
            raise UpdateError(f"所有更新源在 {_PROBE_BUDGET_SECONDS:.0f} 秒内均不可用：{detail}")
        selected = _select_release_source(releases)
        # Once the newest revision is selected, prefer the mirror that
        # responded fastest during the parallel probe; retain same-hash
        # mirrors as deterministic fallbacks for download failures.
        compatible = _order_sources_by_speed(releases)
        fastest = compatible[0] if compatible else selected
        if all(
            item.published_at == selected.published_at
            and item.revision == selected.revision
            and item.archive_sha256 == selected.archive_sha256
            for item in releases
        ):
            selected = fastest
        fallback_urls = tuple(
            item.download_url
            for item in compatible
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
    def _fetch_huggingface_voice_release(deadline: float | None = None) -> ReleaseInfo:
        started = time.monotonic()
        data = UpdateManager._fetch_release_json(
            _HUGGINGFACE_VOICE_UPDATE_URL,
            "Hugging Face 语音包仓库",
            headers=_PAGE_JSON_HEADERS,
            deadline=deadline,
        )
        return _parse_voice_package_release(
            data,
            source_name="Hugging Face",
            file_base_url=_HUGGINGFACE_VOICE_FILE_BASE,
            response_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _fetch_modelscope_voice_release(deadline: float | None = None) -> ReleaseInfo:
        started = time.monotonic()
        data = UpdateManager._fetch_release_json(
            _MODELSCOPE_VOICE_UPDATE_URL,
            "ModelScope 语音包仓库",
            headers=_PAGE_JSON_HEADERS,
            deadline=deadline,
        )
        return _parse_voice_package_release(
            data,
            source_name="ModelScope",
            file_base_url=_MODELSCOPE_VOICE_FILE_BASE,
            response_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _fetch_release_json(
        url: str,
        source_name: str,
        *,
        headers: dict[str, str] | None = None,
        deadline: float | None = None,
    ) -> object:
        last_error: requests.RequestException | None = None
        for attempt in range(1, 4):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0.5:
                if last_error is not None:
                    break
                raise UpdateError(f"{source_name} 探测超时")
            timeout = 10.0 if remaining is None else max(0.5, min(10.0, remaining))
            try:
                response = requests.get(
                    url,
                    timeout=timeout,
                    headers=headers or _PAGE_JSON_HEADERS,
                )
                response.raise_for_status()
                return response.json()
            except ValueError as exc:
                raise UpdateError(f"{source_name} 返回格式异常") from exc
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= 3 or not _is_retryable_request_error(exc):
                    break
                pause = 0.25 * attempt
                if deadline is not None:
                    slack = deadline - time.monotonic()
                    if slack <= 0.5:
                        break
                    pause = min(pause, slack)
                time.sleep(pause)
        raise UpdateError(f"{source_name} 读取失败：{last_error}") from last_error

    def _download_release(self, release: ReleaseInfo, dest_path: Path) -> str:
        """下载发布包，返回流式计算出的 SHA-256（十六进制小写）。

        哈希随下载一起算：清单里的 ``sha256`` 是这份文件唯一需要核对的校验值，下载
        结束后不再把几百兆重新读一遍。
        """
        urls = (release.download_url, *release.fallback_download_urls)
        errors: list[str] = []
        for index, download_url in enumerate(urls):
            try:
                return self._download_url(download_url, dest_path)
            except UpdateError as exc:
                errors.append(str(exc))
                if index + 1 < len(urls):
                    self._info("当前镜像下载失败，正在切换同 revision 备用源...")
        raise UpdateError("；".join(errors) or "没有可用的更新包下载地址")

    def _download_url(self, download_url: str, dest_path: Path) -> str:
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
                    digest = hashlib.sha256()
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(dest_path, "wb") as fp:
                        for chunk in resp.iter_content(chunk_size=512 * 1024):
                            if not chunk:
                                continue
                            fp.write(chunk)
                            digest.update(chunk)
                            downloaded += len(chunk)
                            self._progress(
                                downloaded,
                                total_bytes,
                                f"正在下载资源包… {downloaded // (1024 * 1024)} MB",
                            )
                    if total_bytes and downloaded != total_bytes:
                        raise UpdateError(
                            f"下载内容长度不完整：{downloaded}/{total_bytes} bytes"
                        )
                return digest.hexdigest()
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
