"""Per-user Windows logon startup integration."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from lib.core.logger import get_logger
from lib.script.app.desktop_shortcut import (
    _create_shortcut_via_powershell,
    _get_shortcut_target,
    _paths_refer_same_file,
)


_logger = get_logger(__name__)
_STARTUP_SUBDIR = Path("Microsoft") / "Windows" / "Start Menu" / "Programs" / "Startup"
_SHORTCUT_NAME = "飞行雪绒.lnk"
_LEGACY_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_LEGACY_REG_NAME = "FlyingSnowflake"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def get_project_root() -> Path:
    """Return the directory containing the active launch batch file."""
    if bool(getattr(sys, "frozen", False)):
        return Path(sys.executable).resolve().parent
    return _PROJECT_ROOT


def get_launch_script_path() -> Path:
    return get_project_root() / "启动程序.bat"


def get_user_startup_dir() -> Path | None:
    """Return the current user's Startup folder without requiring elevation."""
    appdata = str(os.environ.get("APPDATA") or "").strip()
    if not appdata:
        return None
    return Path(appdata).expanduser() / _STARTUP_SUBDIR


def get_startup_shortcut_path() -> Path | None:
    startup_dir = get_user_startup_dir()
    return startup_dir / _SHORTCUT_NAME if startup_dir is not None else None


def _read_legacy_registry_value() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _LEGACY_REG_PATH,
            0,
            winreg.KEY_READ,
        ) as key:
            value, _value_type = winreg.QueryValueEx(key, _LEGACY_REG_NAME)
        return str(value or "").strip() or None
    except (FileNotFoundError, OSError):
        return None


def _remove_legacy_registry_value() -> None:
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _LEGACY_REG_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            try:
                winreg.DeleteValue(key, _LEGACY_REG_NAME)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def _shortcut_targets_launch_script(shortcut_path: Path) -> bool:
    if not shortcut_path.is_file():
        return False
    target, _message = _get_shortcut_target(str(shortcut_path))
    return bool(target) and _paths_refer_same_file(str(target), str(get_launch_script_path()))


def is_autostart_enabled() -> bool:
    """Check the actual per-user shortcut, not merely an entry's existence."""
    shortcut_path = get_startup_shortcut_path()
    if shortcut_path is None:
        return False
    return _shortcut_targets_launch_script(shortcut_path)


def _cleanup_temporary_shortcuts(startup_dir: Path) -> None:
    pattern = f".{_SHORTCUT_NAME}.*.tmp.lnk"
    for candidate in startup_dir.glob(pattern):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            _logger.debug("无法清理临时自启动快捷方式: %s", candidate)


def enable_autostart() -> tuple[bool, str]:
    """Create and verify the per-user Startup-folder shortcut."""
    launch_script = get_launch_script_path()
    if not launch_script.is_file():
        return False, f"启动脚本不存在: {launch_script}"

    startup_dir = get_user_startup_dir()
    shortcut_path = get_startup_shortcut_path()
    if startup_dir is None or shortcut_path is None:
        return False, "无法定位当前用户的 Startup 文件夹"

    try:
        startup_dir.mkdir(parents=True, exist_ok=True)
        if _shortcut_targets_launch_script(shortcut_path):
            _remove_legacy_registry_value()
            return True, ""

        temporary_path = startup_dir / f".{_SHORTCUT_NAME}.{uuid.uuid4().hex}.tmp.lnk"
        try:
            ok, message = _create_shortcut_via_powershell(
                shortcut_path=str(temporary_path),
                target_path=str(launch_script),
                working_dir=str(get_project_root()),
                description="飞行雪绒桌面宠物",
                icon_path=str(get_project_root() / "resc" / "icon.ico"),
            )
            if not ok:
                return False, message or "创建 Startup 快捷方式失败"
            if not _shortcut_targets_launch_script(temporary_path):
                return False, "Startup 快捷方式目标校验失败"
            os.replace(str(temporary_path), str(shortcut_path))
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

        if not _shortcut_targets_launch_script(shortcut_path):
            return False, "Startup 快捷方式写入后校验失败"
        _remove_legacy_registry_value()
        _cleanup_temporary_shortcuts(startup_dir)
        return True, ""
    except Exception as exc:
        _logger.warning("创建用户级开机启动失败: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"


def disable_autostart() -> tuple[bool, str]:
    """Remove the canonical shortcut and any legacy per-user Run value."""
    shortcut_path = get_startup_shortcut_path()
    try:
        if shortcut_path is not None:
            shortcut_path.unlink(missing_ok=True)
            startup_dir = shortcut_path.parent
            if startup_dir.is_dir():
                _cleanup_temporary_shortcuts(startup_dir)
        _remove_legacy_registry_value()
        if is_autostart_enabled() or _read_legacy_registry_value() is not None:
            return False, "开机启动项删除后仍然存在"
        return True, ""
    except Exception as exc:
        _logger.warning("删除用户级开机启动失败: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"


def migrate_legacy_autostart() -> None:
    """Migrate a valid old HKCU Run entry once, without elevating privileges."""
    if is_autostart_enabled():
        _remove_legacy_registry_value()
        return

    legacy_value = _read_legacy_registry_value()
    if not legacy_value:
        return
    legacy_target = legacy_value.strip().strip('"')
    if not _paths_refer_same_file(legacy_target, str(get_launch_script_path())):
        return

    ok, message = enable_autostart()
    if not ok:
        _logger.warning("迁移旧开机启动项失败: %s", message)


__all__ = [
    "disable_autostart",
    "enable_autostart",
    "get_launch_script_path",
    "get_project_root",
    "get_startup_shortcut_path",
    "get_user_startup_dir",
    "is_autostart_enabled",
    "migrate_legacy_autostart",
]
