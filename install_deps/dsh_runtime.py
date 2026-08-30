"""Installation workflow for the optional DeepSeek Harness runtime."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path
from typing import Protocol


class InstallerApi(Protocol):
    PROJECT_ROOT: Path
    RESOURCE_SOURCE_HOSTS: dict[str, str]
    dsh_config: object

    def _print_stage(self, step: int, text: str) -> None: ...
    def _print_warn(self, text: str) -> None: ...
    def _dsh_runtime_ready(self) -> tuple[bool, str]: ...
    def _node_tree_ready(self, root: Path) -> tuple[bool, str]: ...
    def _run_dsh_npm_ci(self) -> tuple[bool, str]: ...
    def _dsh_node_urls(self) -> tuple[str, ...]: ...
    def _rmtree_if_exists(self, path: Path, *, ignore_errors: bool = True) -> None: ...
    def _unlink_if_exists(self, path: Path, *, ignore_errors: bool = False) -> None: ...
    def _stream_download_with_progress(self, url: str, dest_path: Path, *, label: str) -> None: ...
    def _extract_zip_with_progress(self, zip_path: Path, extract_root: Path) -> None: ...


_REPLACE_RETRY_DELAYS = (0.15, 0.3, 0.6, 1.0, 1.5)
_RETRYABLE_WINDOWS_ERRORS = {5, 32}


def _rename_with_retry(source: Path, target: Path) -> None:
    """Retry directory replacement while Windows scanners release file handles."""
    for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
        try:
            source.rename(target)
            return
        except OSError as exc:
            retryable = os.name == "nt" and getattr(exc, "winerror", None) in _RETRYABLE_WINDOWS_ERRORS
            if not retryable or attempt >= len(_REPLACE_RETRY_DELAYS):
                raise
            time.sleep(_REPLACE_RETRY_DELAYS[attempt])


def ensure_runtime(api: InstallerApi) -> bool:
    """Install the fixed Node/DSH runtime; application startup never installs it."""
    config = api.dsh_config
    project_root = api.PROJECT_ROOT
    api._print_stage(4, "准备 DeepSeek Harness 办公运行时依赖...")
    ready, detail = api._dsh_runtime_ready()
    if ready:
        print(
            f"  DeepSeek Harness 办公运行时已就绪: Node {config.NODE_VERSION_TEXT}, "
            f"npm {config.NPM_VERSION}, DeepSeek Harness {config.DSH_VERSION}"
        )
        return True
    if detail:
        print(f"  需要准备 DeepSeek Harness 运行时：{detail}")

    node_root = config.node_root(project_root)
    source_error = config.runtime_source_error(project_root)
    if source_error:
        api._print_warn(f"  无法安装 DeepSeek Harness 办公运行时：{source_error}")
        return False

    node_ready, node_detail = api._node_tree_ready(node_root)
    if node_ready:
        installed, install_detail = api._run_dsh_npm_ci()
        if not installed:
            api._print_warn(f"  DeepSeek Harness lockfile 依赖安装失败：{install_detail}")
            return False
        ready, ready_detail = api._dsh_runtime_ready()
        if not ready:
            api._print_warn(f"  DeepSeek Harness 安装后完整性检查失败：{ready_detail}")
            return False
        print(
            f"  DeepSeek Harness 办公运行时已修复: Node {config.NODE_VERSION_TEXT}, "
            f"npm {config.NPM_VERSION}, DeepSeek Harness {config.DSH_VERSION}"
        )
        return True

    if node_detail:
        print(f"  需要重新准备 Node 运行时：{node_detail}")
    archive_name = config.NODE_ARCHIVE_NAME
    urls = api._dsh_node_urls()
    if not urls:
        api._print_warn(f"  没有可用的 Node 下载地址: {archive_name}")
        return False

    node_root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = node_root.with_name(f".{node_root.name}.installing")
    previous_root = node_root.with_name(f".{node_root.name}.previous")
    last_detail = "Node ZIP 下载失败"
    try:
        api._rmtree_if_exists(staging_root, ignore_errors=False)
        with tempfile.TemporaryDirectory(prefix="aemeath-dsh-node-") as temp_dir:
            archive_path = Path(temp_dir) / archive_name
            part_path = archive_path.with_name(archive_path.name + ".part")
            for index, url in enumerate(urls, start=1):
                source_name = api.RESOURCE_SOURCE_HOSTS.get(
                    (urllib.parse.urlsplit(url).hostname or "").lower(),
                    f"镜像 {index}",
                )
                try:
                    print(f"  下载 Node 运行时 [{index}/{len(urls)}] ({source_name})")
                    api._unlink_if_exists(part_path, ignore_errors=True)
                    api._stream_download_with_progress(
                        url,
                        part_path,
                        label=f"Node {config.NODE_VERSION_TEXT}",
                    )
                    digest = hashlib.sha256(part_path.read_bytes()).hexdigest()
                    if digest.lower() != config.NODE_ARCHIVE_SHA256.lower():
                        raise ValueError(
                            "Node ZIP SHA-256 不匹配，"
                            f"期望 {config.NODE_ARCHIVE_SHA256}，实际 {digest}"
                        )
                    part_path.replace(archive_path)
                    break
                except (urllib.error.URLError, OSError, ValueError, zipfile.BadZipFile) as exc:
                    last_detail = f"{source_name}：{exc}"
                    api._print_warn(f"  Node 下载/校验失败：{last_detail}")
                    api._unlink_if_exists(part_path, ignore_errors=True)
            else:
                api._print_warn(f"  Node 运行时准备失败：{last_detail}")
                return False

            extract_root = Path(temp_dir) / "extract"
            api._extract_zip_with_progress(archive_path, extract_root)
            source_root = extract_root / f"node-v{config.NODE_VERSION}-win-x64"
            if not source_root.is_dir():
                raise ValueError(f"Node ZIP 缺少目录 {source_root.name}")
            shutil.move(str(source_root), str(staging_root))

        ready, detail = api._node_tree_ready(staging_root)
        if not ready:
            raise RuntimeError(f"解压后的 Node 运行时无效：{detail}")

        api._rmtree_if_exists(previous_root, ignore_errors=False)
        if node_root.exists():
            _rename_with_retry(node_root, previous_root)
        try:
            _rename_with_retry(staging_root, node_root)
        except Exception:
            if previous_root.exists() and not node_root.exists():
                _rename_with_retry(previous_root, node_root)
            raise
        api._rmtree_if_exists(previous_root, ignore_errors=True)

        installed, detail = api._node_tree_ready(node_root)
        if not installed:
            raise RuntimeError(detail)
        installed, detail = api._run_dsh_npm_ci()
        if not installed:
            raise RuntimeError(detail)
        ready, detail = api._dsh_runtime_ready()
        if not ready:
            raise RuntimeError(f"DeepSeek Harness 安装后完整性检查失败：{detail}")
        print(
            f"  DeepSeek Harness 办公运行时已安装: Node {config.NODE_VERSION_TEXT}, "
            f"npm {config.NPM_VERSION}, DeepSeek Harness {config.DSH_VERSION}"
        )
        return True
    except Exception as exc:
        api._print_warn(
            "  DeepSeek Harness 办公运行时安装失败："
            f"{exc}（临时目录：{staging_root}；目标目录：{node_root}）"
        )
        api._rmtree_if_exists(staging_root, ignore_errors=True)
        return False


def should_install() -> bool:
    """Ask before downloading the optional DeepSeek Harness sidecar."""
    override = str(os.environ.get("FLYING_SNOW_INSTALL_DSH", "") or "").strip().lower()
    if override in {"1", "y", "yes", "true", "on"}:
        return True
    if override in {"0", "n", "no", "false", "off"}:
        return False
    try:
        answer = input("\n是否安装办公模式 DeepSeek Harness 运行时？(Y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer not in {"n", "no"}
