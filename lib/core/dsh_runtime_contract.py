"""Standard-library-only contract for the optional DSH office runtime.

The dependency installer and the application process must agree on the exact
Node/npm/DSH versions and on the generated runtime layout.  Keep this module
free of application configuration and third-party imports so the installer
can load it before Python dependencies are available.
"""

from __future__ import annotations

import json
from pathlib import Path


DSH_VERSION = "0.1.0-rc.6"
NODE_VERSION = "24.13.0"
NODE_VERSION_TEXT = f"v{NODE_VERSION}"
NPM_VERSION = "11.6.2"
NODE_ARCHIVE_NAME = f"node-v{NODE_VERSION}-win-x64.zip"
NODE_DIRECTORY_NAME = f"node-{NODE_VERSION}-win-x64"
NODE_ARCHIVE_SHA256 = "ca2742695be8de44027d71b3f53a4bdb36009b95575fe1ae6f7f0b5ce091cb88"

NODE_DOWNLOAD_URLS = (
    f"https://npmmirror.com/mirrors/node/v{NODE_VERSION}/{NODE_ARCHIVE_NAME}",
    f"https://mirrors.cloud.tencent.com/nodejs-release/v{NODE_VERSION}/{NODE_ARCHIVE_NAME}",
    f"https://mirrors.aliyun.com/nodejs-release/v{NODE_VERSION}/{NODE_ARCHIVE_NAME}",
    f"https://repo.huaweicloud.com/nodejs/v{NODE_VERSION}/{NODE_ARCHIVE_NAME}",
    f"https://nodejs.org/dist/v{NODE_VERSION}/{NODE_ARCHIVE_NAME}",
)

RUNTIME_SOURCE_FILES = (
    "package.json",
    "package-lock.json",
    "profile/package.json",
    "profile/cordis.patch.yml",
    "bridge/package.json",
    "bridge/index.mjs",
    "bridge/credentials.mjs",
)

REQUIRED_DSH_PACKAGES = (
    "dsh",
    "dsh-agent",
    "dsh-credentials",
    "dsh-llm",
    "dsh-llm-pi-ai",
    "dsh-session",
    "dsh-system-prompt",
)


def node_root(project_root: Path) -> Path:
    return Path(project_root) / "resc" / NODE_DIRECTORY_NAME


def node_executable(project_root: Path) -> Path:
    return node_root(project_root) / "node.exe"


def npm_cli_path(project_root: Path) -> Path:
    return node_root(project_root) / "node_modules" / "npm" / "bin" / "npm-cli.js"


def dsh_runtime_root(project_root: Path) -> Path:
    return Path(project_root) / "services" / "dsh-office-runtime"


def dsh_entry_path(project_root: Path) -> Path:
    return (
        dsh_runtime_root(project_root)
        / "node_modules"
        / "@deepseek-ai"
        / "dsh"
        / "lib"
        / "bin.js"
    )


def _read_json_object(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def runtime_source_error(project_root: Path) -> str:
    """Validate the source bundle needed by both npm ci and DSH startup."""
    root = dsh_runtime_root(project_root)
    missing = [
        relative
        for relative in RUNTIME_SOURCE_FILES
        if not (root / relative).is_file() or (root / relative).stat().st_size <= 0
    ]
    if missing:
        return f"DSH 办公运行时源码不完整：缺少 {missing[0]}"

    manifest = _read_json_object(root / "package.json")
    lockfile = _read_json_object(root / "package-lock.json")
    profile = _read_json_object(root / "profile" / "package.json")
    bridge = _read_json_object(root / "bridge" / "package.json")
    if None in (manifest, lockfile, profile, bridge):
        return "DSH 办公运行时源码清单无法读取"

    engines = manifest.get("engines") if isinstance(manifest.get("engines"), dict) else {}
    dependencies = (
        manifest.get("dependencies")
        if isinstance(manifest.get("dependencies"), dict)
        else {}
    )
    if (
        engines.get("node") != NODE_VERSION
        or engines.get("npm") != NPM_VERSION
        or dependencies.get("@deepseek-ai/dsh") != DSH_VERSION
    ):
        return "DSH package.json 的 Node/npm/DSH 版本锁定不一致"

    packages = lockfile.get("packages") if isinstance(lockfile.get("packages"), dict) else {}
    lock_root = packages.get("") if isinstance(packages.get(""), dict) else {}
    lock_dependencies = (
        lock_root.get("dependencies")
        if isinstance(lock_root.get("dependencies"), dict)
        else {}
    )
    locked_dsh = packages.get("node_modules/@deepseek-ai/dsh")
    if (
        lockfile.get("lockfileVersion") != 3
        or lock_dependencies.get("@deepseek-ai/dsh") != DSH_VERSION
        or not isinstance(locked_dsh, dict)
        or locked_dsh.get("version") != DSH_VERSION
    ):
        return "DSH package-lock.json 与固定 DSH 版本不一致"

    profile_dsh = profile.get("dsh") if isinstance(profile.get("dsh"), dict) else {}
    profile_settings = (
        profile_dsh.get("profile")
        if isinstance(profile_dsh.get("profile"), dict)
        else {}
    )
    profile_dependencies = (
        profile.get("dependencies")
        if isinstance(profile.get("dependencies"), dict)
        else {}
    )
    if (
        profile_settings.get("bundles") != ["@deepseek-ai/dsh-base"]
        or profile_dependencies.get("@fsv/dsh-office-bridge") != "0.1.0"
        or bridge.get("name") != "@fsv/dsh-office-bridge"
    ):
        return "DSH 办公 profile 或 bridge 清单不匹配"
    return ""


def installed_runtime_error(project_root: Path) -> str:
    """Validate the locked production packages consumed by the office profile."""
    root = dsh_runtime_root(project_root)
    if not dsh_entry_path(project_root).is_file():
        return "DSH 启动入口不完整"
    for package in REQUIRED_DSH_PACKAGES:
        manifest = (
            root
            / "node_modules"
            / "@deepseek-ai"
            / package
            / "package.json"
        )
        payload = _read_json_object(manifest)
        if payload is None:
            return f"DSH 依赖不完整：@deepseek-ai/{package}"
        if payload.get("version") != DSH_VERSION:
            return f"DSH 依赖版本不匹配：@deepseek-ai/{package}"
    return ""
