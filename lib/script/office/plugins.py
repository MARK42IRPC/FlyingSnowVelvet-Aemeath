"""办公插件：列出、安装与删除办公 profile 加载的 DSH 插件包。

DSH 的插件是 npm 包：profile 的 `package.json` 里 `dsh.profile.bundles` 列出要加载的
包，包本身放在 profile 的 node_modules 下。程序自带的 `@deepseek-ai/dsh-base` 是内置
bundle，用户装的额外包登记在用户态 `plugins.json` 里，启动 provisioning 时再合并进
profile 的 package.json —— 这样重新解压程序包也不会把用户装的插件弄丢。

安装只做「复制目录 + 登记包名」：包必须是带 `package.json` 的普通目录，复制到
profile 的 node_modules 下同名位置。删除只删登记过的那份，绝不动程序自带的运行时。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from config.user_storage_paths import get_user_state_dir
from lib.core.logger import get_logger

logger = get_logger(__name__)

#: 用户态插件登记文件名（放在 office 状态目录下）。
PLUGIN_REGISTRY_FILE = "plugins.json"
#: 办公 profile 的目录名，与 `runtime._provision_profile` 保持一致。
PROFILE_DIR_NAME = "fsv-office"
_PACKAGE_MANIFEST = "package.json"

_registry_lock = threading.Lock()


class PluginError(RuntimeError):
    """插件安装/删除失败；消息直接展示给用户。"""


@dataclass(frozen=True)
class OfficePlugin:
    """一个 DSH 插件包：包名、版本、说明、位置，以及它是否属于程序自带。"""

    name: str
    version: str
    description: str
    path: Path
    bundled: bool

    @property
    def removable(self) -> bool:
        """内置 bundle 是程序运行的基础，不允许在界面上删除。"""
        return not self.bundled


def profile_dir() -> Path:
    """当前生效的办公 profile（provisioning 会把源 profile 复制到这里）。"""
    return get_user_state_dir("office", "dsh-home") / "profiles" / PROFILE_DIR_NAME


def source_profile_dir() -> Path:
    """程序自带的源 profile。"""
    from .runtime import runtime_root

    return runtime_root() / "profile"


def profile_node_modules() -> Path:
    return profile_dir() / "node_modules"


def _package_dir_parts(name: str) -> tuple[str, ...] | None:
    """把包名拆成 node_modules 下的相对段；绝对路径或含 `.`/`..` 时返回 None。

    包名后面直接拼目录，不校验的话 `package.json` 里写 `../../x` 就能把包复制到
    profile 之外，而卸载与登记都以 node_modules 为根，装出来的目录会变成孤儿。
    """
    text = str(name or "").strip().replace("\\", "/")
    if not text or text.startswith("/"):
        return None
    parts = tuple(part for part in text.split("/") if part)
    if not parts or any(part in {".", ".."} or ":" in part for part in parts):
        return None
    return parts


def registry_path() -> Path:
    return get_user_state_dir("office", PLUGIN_REGISTRY_FILE)


def _read_registry() -> dict:
    try:
        payload = json.loads(registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"bundles": []}
    if not isinstance(payload, dict):
        return {"bundles": []}
    bundles = payload.get("bundles")
    if not isinstance(bundles, list):
        bundles = []
    return {"bundles": [str(name).strip() for name in bundles if str(name).strip()]}


def _write_registry(payload: dict) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def registered_bundles() -> list[str]:
    """用户登记过的额外 bundle 包名。"""
    with _registry_lock:
        return list(_read_registry()["bundles"])


def builtin_bundles() -> list[str]:
    """源 profile 声明的内置 bundle 包名。"""
    try:
        payload = json.loads((source_profile_dir() / _PACKAGE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    dsh = payload.get("dsh") if isinstance(payload, dict) else None
    profile = dsh.get("profile") if isinstance(dsh, dict) else None
    bundles = profile.get("bundles") if isinstance(profile, dict) else None
    if not isinstance(bundles, list):
        return []
    return [str(name).strip() for name in bundles if str(name).strip()]


def _package_dir(name: str) -> Path | None:
    """按 node_modules 解析顺序找包目录：先 profile，再程序自带运行时。"""
    parts = _package_dir_parts(name)
    if parts is None:
        return None
    candidates = [profile_node_modules()]
    try:
        from .runtime import runtime_root

        candidates.append(runtime_root() / "node_modules")
    except Exception:
        pass
    for root in candidates:
        directory = Path(root).joinpath(*parts)
        if (directory / _PACKAGE_MANIFEST).is_file():
            return directory
    return None


def read_plugin(name: str, *, bundled: bool) -> OfficePlugin | None:
    """读取一个插件包；没有 package.json 就不是插件。"""
    directory = _package_dir(name)
    if directory is None:
        return None
    try:
        payload = json.loads((directory / _PACKAGE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return OfficePlugin(
        name=str(payload.get("name") or name),
        version=str(payload.get("version") or ""),
        description=str(payload.get("description") or ""),
        path=directory,
        bundled=bundled,
    )


def list_plugins() -> list[OfficePlugin]:
    """列出内置 bundle 与用户安装的插件，按包名排序。"""
    found: dict[str, OfficePlugin] = {}
    for name in registered_bundles():
        plugin = read_plugin(name, bundled=False)
        if plugin is not None:
            found[plugin.name] = plugin
        else:
            found[name] = OfficePlugin(
                name=name,
                version="",
                description="已登记但找不到包目录，可能在安装时被中断",
                path=profile_node_modules() / name,
                bundled=False,
            )
    for name in builtin_bundles():
        plugin = read_plugin(name, bundled=True)
        if plugin is not None:
            found.setdefault(plugin.name, plugin)
    return sorted(found.values(), key=lambda plugin: plugin.name.casefold())


def install_plugin(source: Path) -> OfficePlugin:
    """把用户选的插件目录复制进 profile 的 node_modules 并登记包名。"""
    source = Path(source)
    if source.is_file() and source.name == _PACKAGE_MANIFEST:
        source = source.parent
    if not source.is_dir() or not (source / _PACKAGE_MANIFEST).is_file():
        raise PluginError(f"插件目录缺少 {_PACKAGE_MANIFEST}：{source}")
    try:
        payload = json.loads((source / _PACKAGE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise PluginError(f"读取插件清单失败：{exc}") from exc
    name = str(payload.get("name") or "").strip() if isinstance(payload, dict) else ""
    if not name:
        raise PluginError("插件 package.json 里没有 name 字段")
    if name in builtin_bundles():
        raise PluginError(f"内置插件不需要重复安装：{name}")
    if name in registered_bundles():
        raise PluginError(f"已经安装过同名插件：{name}")
    parts = _package_dir_parts(name)
    if parts is None:
        raise PluginError(f"插件名不能是路径：{name!r}")
    target = profile_node_modules().joinpath(*parts)
    if target.exists():
        raise PluginError(f"插件目录已存在：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    with _registry_lock:
        payload = _read_registry()
        bundles = list(payload["bundles"])
        if name not in bundles:
            bundles.append(name)
        payload["bundles"] = bundles
        _write_registry(payload)
    logger.info("[OfficePlugins] 安装插件 name=%s target=%s", name, target)
    return read_plugin(name, bundled=False) or OfficePlugin(name, "", "", target, False)


def remove_plugin(name: str) -> Path:
    """删除用户安装的插件：先注销登记，再删掉复制进来的包目录。"""
    wanted = str(name or "").strip()
    if not wanted:
        raise PluginError("没有指定要删除的插件")
    if wanted in builtin_bundles():
        raise PluginError(f"内置插件随程序发布，不能在这里删除：{wanted}")
    parts = _package_dir_parts(wanted)
    if parts is None:
        raise PluginError(f"没有安装过这个插件：{wanted}")
    with _registry_lock:
        payload = _read_registry()
        bundles = list(payload["bundles"])
        if wanted not in bundles:
            raise PluginError(f"没有安装过这个插件：{wanted}")
        payload["bundles"] = [item for item in bundles if item != wanted]
        _write_registry(payload)
    target = profile_node_modules().joinpath(*parts)
    root = profile_node_modules().resolve()
    try:
        resolved = target.resolve()
    except OSError:
        resolved = target
    if target.is_dir() and (resolved == root or root in resolved.parents):
        shutil.rmtree(target)
    logger.info("[OfficePlugins] 删除插件 name=%s path=%s", wanted, target)
    return target


def apply_registered_bundles(profile_package_path: Path) -> None:
    """把用户登记的 bundle 合并进 profile 的 package.json。

    provisioning 每次启动都会用源 profile 覆盖这份 package.json，所以用户装的插件
    必须在这里补回去；没有登记过任何插件时保持原样。
    """
    bundles = registered_bundles()
    if not bundles:
        return
    path = Path(profile_package_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    if not isinstance(payload, dict):
        return
    dsh = payload.setdefault("dsh", {})
    if not isinstance(dsh, dict):
        return
    profile = dsh.setdefault("profile", {})
    if not isinstance(profile, dict):
        return
    existing = profile.get("bundles")
    merged = list(existing) if isinstance(existing, list) else []
    for name in bundles:
        if name not in merged:
            merged.append(name)
    profile["bundles"] = merged
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "OfficePlugin",
    "PLUGIN_REGISTRY_FILE",
    "PluginError",
    "apply_registered_bundles",
    "builtin_bundles",
    "install_plugin",
    "list_plugins",
    "profile_dir",
    "profile_node_modules",
    "read_plugin",
    "registered_bundles",
    "registry_path",
    "remove_plugin",
    "source_profile_dir",
]
