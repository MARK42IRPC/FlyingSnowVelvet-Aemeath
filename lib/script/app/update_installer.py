"""退出主程序后覆盖安装更新包并重新启动桌宠。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Sequence

from lib.script.app.restart import (
    _detached_kwargs,
    _is_process_running,
    build_restart_command,
)


_PROTECTED_ROOTS = ("logs", "resc/user", "resc/models")
_PROTECTED_FILES = ("py.ini",)


def _normalize_relative_path(path: Path) -> str:
    return "/".join(part for part in path.parts if part not in ("", "."))


def _is_protected_path(path: Path) -> bool:
    relative = _normalize_relative_path(path)
    if not relative:
        return False
    if relative in _PROTECTED_FILES:
        return True
    return any(
        relative == root or relative.startswith(root + "/")
        for root in _PROTECTED_ROOTS
    )


def _write_update_log(project_root: Path, message: str) -> None:
    try:
        log_dir = project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "update.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def _validate_member_path(extract_root: Path, member_name: str) -> Path:
    normalized = str(member_name or "").replace("\\", "/")
    member = Path(normalized)
    if member.is_absolute() or ".." in member.parts:
        raise ValueError(f"更新包包含不安全路径: {member_name}")
    destination = (extract_root / member).resolve()
    try:
        destination.relative_to(extract_root.resolve())
    except ValueError as exc:
        raise ValueError(f"更新包路径越界: {member_name}") from exc
    return destination


def validate_update_archive(archive_path: Path) -> None:
    """在主程序退出前完成 ZIP 完整性与路径检查。"""
    archive = Path(archive_path)
    if not archive.is_file():
        raise ValueError("更新包不存在")
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            if not bundle.infolist():
                raise ValueError("更新包为空")
            probe_root = Path(tempfile.gettempdir()) / "fsv-update-path-probe"
            for member in bundle.infolist():
                _validate_member_path(probe_root, member.filename)
            broken = bundle.testzip()
            if broken:
                raise ValueError(f"更新包文件损坏: {broken}")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"更新包不是有效的 ZIP 文件: {exc}") from exc


def _extract_archive(archive_path: Path, extract_root: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as bundle:
        for member in bundle.infolist():
            destination = _validate_member_path(extract_root, member.filename)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member, "r") as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)


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
    source_root: Path,
    project_root: Path,
) -> list[tuple[Path, Path]]:
    operations: list[tuple[Path, Path]] = []
    for root, dirs, files in os.walk(source_root):
        relative_dir = Path(root).relative_to(source_root)
        if relative_dir != Path(".") and _is_protected_path(relative_dir):
            dirs[:] = []
            continue
        for directory in list(dirs):
            relative = (
                relative_dir / directory
                if relative_dir != Path(".")
                else Path(directory)
            )
            if _is_protected_path(relative):
                dirs.remove(directory)
        for file_name in files:
            relative = (
                relative_dir / file_name
                if relative_dir != Path(".")
                else Path(file_name)
            )
            if _is_protected_path(relative):
                continue
            operations.append((Path(root) / file_name, project_root / relative))
    return operations


def install_update_archive(
    archive_path: Path,
    project_root: Path,
    state_path: Path,
    release: dict,
) -> int:
    """覆盖项目文件并在全部成功后写入已安装状态。"""
    project = Path(project_root).resolve()
    archive = Path(archive_path).resolve()
    state = Path(state_path).resolve()
    validate_update_archive(archive)
    with tempfile.TemporaryDirectory(prefix="fsv-update-extract-") as temp_dir:
        extracted_root = Path(temp_dir)
        _extract_archive(archive, extracted_root)
        content_root = _resolve_content_root(extracted_root)
        operations = _collect_copy_operations(content_root, project)
        if not operations:
            raise ValueError("更新包中没有可安装文件")
        for source, destination in operations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    payload = {
        "version": str(release.get("tag") or "latest"),
        "installed_at": str(release.get("published_at") or ""),
        "revision": str(release.get("revision") or ""),
        "source": str(release.get("source") or ""),
    }
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(operations)


def run_update_installer(payload: dict, max_wait: float = 45.0) -> int:
    """等待旧实例退出，完成覆盖后启动更新后的桌宠。"""
    project_root = Path(str(payload["project_root"]))
    archive_path = Path(str(payload["archive_path"]))
    state_path = Path(str(payload["state_path"]))
    parent_pid = int(payload["parent_pid"])
    command = payload["restart_command"]
    release = payload["release"]
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise ValueError("无效的应用启动命令")
    if not isinstance(release, dict):
        raise ValueError("无效的更新版本信息")

    _write_update_log(project_root, f"更新 helper pid={os.getpid()} 等待主进程 pid={parent_pid}")
    deadline = time.monotonic() + max(0.0, float(max_wait))
    while _is_process_running(parent_pid) and time.monotonic() < deadline:
        time.sleep(0.15)
    if _is_process_running(parent_pid):
        _write_update_log(project_root, "等待主进程退出超时，已取消覆盖安装")
        return 2

    try:
        count = install_update_archive(archive_path, project_root, state_path, release)
        _write_update_log(project_root, f"覆盖安装完成，共写入 {count} 个文件")
        try:
            process = subprocess.Popen(list(command), **_detached_kwargs())
        except OSError:
            process = subprocess.Popen(
                list(command),
                **_detached_kwargs(include_breakaway=False),
            )
        _write_update_log(project_root, f"已启动更新后的桌宠 pid={process.pid}")
        shutil.rmtree(archive_path.parent, ignore_errors=True)
        return 0
    except Exception as exc:
        _write_update_log(project_root, f"覆盖安装失败: {exc}")
        return 1


def launch_update_installer(
    archive_path: Path,
    project_root: Path,
    state_path: Path,
    release: dict,
) -> subprocess.Popen:
    """创建脱离当前进程树的更新 helper。"""
    restart_command = build_restart_command()
    payload = {
        "parent_pid": os.getpid(),
        "project_root": str(Path(project_root).resolve()),
        "archive_path": str(Path(archive_path).resolve()),
        "state_path": str(Path(state_path).resolve()),
        "release": dict(release),
        "restart_command": restart_command,
    }
    helper_args = [
        "--fsv-update-helper",
        json.dumps(payload, ensure_ascii=False),
    ]
    if bool(getattr(sys, "frozen", False)):
        helper_executable = Path(archive_path).parent / "fsv-update-helper.exe"
        shutil.copy2(sys.executable, helper_executable)
        helper_command = [str(helper_executable), *helper_args]
    else:
        entry = Path(project_root) / "lib" / "core" / "qt_desktop_pet.py"
        helper_command = [sys.executable, str(entry), *helper_args]
    try:
        return subprocess.Popen(helper_command, **_detached_kwargs())
    except OSError:
        return subprocess.Popen(
            helper_command,
            **_detached_kwargs(include_breakaway=False),
        )
