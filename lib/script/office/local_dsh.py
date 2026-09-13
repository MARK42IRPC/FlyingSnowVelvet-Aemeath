"""探测用户本机安装的 DeepSeek Harness。

内置 DSH 固定在 `services/dsh-office-runtime/node_modules/@deepseek-ai/dsh`，
当用户机器上已经全局安装过同一套 Harness 时，办公后端可以选择直接复用本机
的 Node 与 DSH 入口，只把我们自己的 profile 与 bridge 写进 `DSH_HOME`。

探测只读：不安装、不修改本机 Node/npm 环境，也不接受版本明显不兼容的安装。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lib.core import dsh_runtime_contract as dsh_config
from lib.core.process_utils import hidden_process_kwargs

LOCAL_DSH_ENV = "FSV_OFFICE_LOCAL_DSH"
DISPLAY_NAME = "本机 DeepSeek Harness"

_SCOPE_DIRECTORY = "@deepseek-ai"
_PACKAGE_DIRECTORY = "dsh"
_PROBE_TIMEOUT_SECONDS = 6


@dataclass(frozen=True)
class LocalDshInstall:
    """一次成功的本机 DSH 探测结果。"""

    package_root: Path
    node_modules_root: Path
    entry: Path
    version: str
    source: str
    supported: bool
    reason: str


_CACHE: LocalDshInstall | None = None
_PROBED = False
_ROOT_CACHE: list[tuple[Path, str]] | None = None
_NODE_CACHE: dict[str, str | None] = {}


def reset_local_dsh_cache() -> None:
    """清除探测缓存（测试与用户手动重新探测时使用）。"""
    global _CACHE, _PROBED, _ROOT_CACHE
    _CACHE = None
    _PROBED = False
    _ROOT_CACHE = None
    _NODE_CACHE.clear()


def _read_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _version_key(value: object) -> tuple[int, int] | None:
    match = re.match(r"^\s*v?(\d+)\.(\d+)", str(value or ""))
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def is_supported_version(version: object) -> bool:
    """只接受与内置 DSH 同一 major.minor 的本机版本。"""
    expected = _version_key(dsh_config.DSH_VERSION)
    return expected is not None and _version_key(version) == expected


def _resolve_entry(package_root: Path) -> Path | None:
    manifest = _read_json_object(package_root / "package.json")
    candidates: list[Path] = []
    bin_field = manifest.get("bin")
    if isinstance(bin_field, str):
        candidates.append(package_root / bin_field)
    elif isinstance(bin_field, dict):
        preferred = bin_field.get(_PACKAGE_DIRECTORY)
        if isinstance(preferred, str):
            candidates.append(package_root / preferred)
        candidates.extend(
            package_root / value
            for value in bin_field.values()
            if isinstance(value, str)
        )
    candidates.extend((
        package_root / "lib" / "bin.js",
        package_root / "bin.js",
        package_root / "lib" / "bin" / "dsh.js",
        package_root / "dist" / "bin.js",
    ))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _missing_packages(node_modules_root: Path) -> list[str]:
    missing = []
    for package in dsh_config.REQUIRED_DSH_PACKAGES:
        manifest = node_modules_root / _SCOPE_DIRECTORY / package / "package.json"
        if not manifest.is_file():
            missing.append(f"{_SCOPE_DIRECTORY}/{package}")
    return missing


def _validate_candidate(package_root: Path, source: str) -> LocalDshInstall | None:
    if package_root is None or not package_root.is_dir():
        return None
    manifest_path = package_root / "package.json"
    if not manifest_path.is_file():
        return None
    manifest = _read_json_object(manifest_path)
    name = str(manifest.get("name") or "")
    if name not in (f"{_SCOPE_DIRECTORY}/{_PACKAGE_DIRECTORY}", _PACKAGE_DIRECTORY):
        return None
    version = str(manifest.get("version") or "").strip()
    entry = _resolve_entry(package_root)
    # 包位于 <node_modules>/@deepseek-ai/dsh，解析到真正的 node_modules 根。
    node_modules_root = package_root.parent.parent
    if entry is None:
        return LocalDshInstall(
            package_root=package_root,
            node_modules_root=node_modules_root,
            entry=package_root,
            version=version,
            source=source,
            supported=False,
            reason=f"{DISPLAY_NAME}入口缺失：{package_root}",
        )
    if (
        package_root.parent.name.lower() != _SCOPE_DIRECTORY.lower()
        or node_modules_root.name.lower() != "node_modules"
    ):
        return LocalDshInstall(
            package_root=package_root,
            node_modules_root=node_modules_root,
            entry=entry,
            version=version,
            source=source,
            supported=False,
            reason=f"{DISPLAY_NAME}不在 node_modules 目录中：{package_root}",
        )
    missing = _missing_packages(node_modules_root)
    if missing:
        return LocalDshInstall(
            package_root=package_root,
            node_modules_root=node_modules_root,
            entry=entry,
            version=version,
            source=source,
            supported=False,
            reason=f"{DISPLAY_NAME}依赖不完整：缺少 {missing[0]}",
        )
    if not is_supported_version(version):
        return LocalDshInstall(
            package_root=package_root,
            node_modules_root=node_modules_root,
            entry=entry,
            version=version,
            source=source,
            supported=False,
            reason=(
                f"{DISPLAY_NAME}版本不受支持：{version or '未知'}，"
                f"需要 {dsh_config.DSH_VERSION} 同系列版本"
            ),
        )
    return LocalDshInstall(
        package_root=package_root,
        node_modules_root=node_modules_root,
        entry=entry,
        version=version,
        source=source,
        supported=True,
        reason="",
    )


def _override_candidates(raw_path: Path) -> list[Path]:
    candidates: list[Path] = []
    if raw_path.is_file():
        candidates.append(raw_path.parent)
        candidates.append(raw_path.parent.parent)
    else:
        candidates.extend((
            raw_path,
            raw_path / "node_modules" / _SCOPE_DIRECTORY / _PACKAGE_DIRECTORY,
            raw_path / _SCOPE_DIRECTORY / _PACKAGE_DIRECTORY,
        ))
    return candidates


def _npm_global_prefix() -> Path | None:
    npm = shutil.which("npm")
    if not npm:
        return None
    try:
        result = subprocess.run(
            [npm, "prefix", "-g"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
            **hidden_process_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    if not text:
        return None
    return Path(text.splitlines()[-1].strip())


def _candidate_roots() -> list[tuple[Path, str]]:
    global _ROOT_CACHE
    if _ROOT_CACHE is not None:
        return _ROOT_CACHE
    candidates: list[tuple[Path, str]] = []
    override = str(os.environ.get(LOCAL_DSH_ENV, "") or "").strip()
    if override:
        candidates.extend((path, f"{LOCAL_DSH_ENV}") for path in _override_candidates(Path(override)))

    which = shutil.which(_PACKAGE_DIRECTORY)
    if which:
        base = Path(which).resolve().parent
        candidates.append((
            base / "node_modules" / _SCOPE_DIRECTORY / _PACKAGE_DIRECTORY,
            "PATH 中的 dsh",
        ))

    prefix = _npm_global_prefix()
    if prefix is not None:
        candidates.append((
            prefix / "node_modules" / _SCOPE_DIRECTORY / _PACKAGE_DIRECTORY,
            "npm 全局目录",
        ))

    appdata = str(os.environ.get("APPDATA", "") or "").strip()
    if appdata:
        candidates.append((
            Path(appdata) / "npm" / "node_modules" / _SCOPE_DIRECTORY / _PACKAGE_DIRECTORY,
            "%APPDATA%\\npm",
        ))

    unique: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path, source in candidates:
        try:
            key = str(path.resolve()).casefold()
        except OSError:
            key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append((path, source))
    _ROOT_CACHE = unique
    return unique


def probe_local_dsh() -> LocalDshInstall | None:
    """探测并缓存本机 DSH；找不到可用安装时返回 None。"""
    global _CACHE, _PROBED
    if _PROBED:
        return _CACHE
    _PROBED = True
    for package_root, source in _candidate_roots():
        install = _validate_candidate(package_root, source)
        if install is not None and install.supported:
            _CACHE = install
            return install
    _CACHE = None
    return None


def _find_any_install() -> tuple[LocalDshInstall | None, str]:
    """返回探测到的第一个候选（含不受支持的）与不可用原因。"""
    rejected: LocalDshInstall | None = None
    for package_root, source in _candidate_roots():
        install = _validate_candidate(package_root, source)
        if install is None:
            continue
        if install.supported:
            return install, ""
        if rejected is None:
            rejected = install
    if rejected is not None:
        return rejected, rejected.reason
    return None, f"未探测到 {DISPLAY_NAME}（npm 全局安装的 @deepseek-ai/dsh）"


def _node_probe(node: str) -> str:
    try:
        result = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
            **hidden_process_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip()


def resolve_local_node_executable(project_root: Path | None = None) -> str | None:
    """本机 DSH 使用的 Node：优先包内置版本，其次可运行的系统 Node。"""
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[3]
    key = str(root)
    if key in _NODE_CACHE:
        return _NODE_CACHE[key]
    bundled = dsh_config.node_executable(root)
    if bundled.is_file():
        _NODE_CACHE[key] = str(bundled)
        return _NODE_CACHE[key]
    system_node = shutil.which("node")
    if not system_node:
        _NODE_CACHE[key] = None
        return None
    _NODE_CACHE[key] = system_node if _node_probe(system_node) else None
    return _NODE_CACHE[key]


def local_dsh_status(project_root: Path | None = None) -> dict:
    """给设置面板/日志使用的只读探测结果。"""
    install, reason = _find_any_install()
    node = resolve_local_node_executable(project_root)
    if install is None:
        return {
            "available": False,
            "path": "",
            "version": "",
            "source": "",
            "node": node or "",
            "reason": reason,
        }
    available = install.supported and node is not None
    if install.supported and node is None:
        reason = f"{DISPLAY_NAME}需要 Node 运行时，请安装 Node 或重新运行“安装依赖.bat”"
    return {
        "available": available,
        "path": str(install.package_root),
        "version": install.version,
        "source": install.source,
        "node": node or "",
        "reason": "" if available else (reason or install.reason),
    }
