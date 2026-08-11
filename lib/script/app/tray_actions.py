"""Toolkit-neutral system tray actions."""
from __future__ import annotations

import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from config.user_storage_paths import get_user_cache_dir
from lib.core.logger import get_logger


_logger = get_logger(__name__)
AUTHOR_PAGE_URL = "https://space.bilibili.com/486401719"
_MUSIC_CACHE_PLATFORMS = ("netease", "qq", "kugou", "local", "other")


@dataclass(frozen=True)
class TrayActionResult:
    """User-facing result returned by a potentially blocking tray action."""

    message: str
    success: bool = True
    enabled: bool | None = None


def prepare_autostart_state() -> bool:
    """Migrate the legacy entry and return the actual shortcut state."""
    try:
        from lib.script.app.autostart import is_autostart_enabled, migrate_legacy_autostart

        migrate_legacy_autostart()
        return bool(is_autostart_enabled())
    except Exception as exc:
        _logger.warning("检查开机启动状态失败: %s", exc)
        return False


def set_autostart_enabled(enabled: bool) -> TrayActionResult:
    """Set and verify the per-user Startup shortcut."""
    target = bool(enabled)
    try:
        from lib.script.app.autostart import (
            disable_autostart,
            enable_autostart,
            is_autostart_enabled,
        )

        success, detail = enable_autostart() if target else disable_autostart()
        actual = bool(is_autostart_enabled())
    except Exception as exc:
        _logger.error("切换开机启动失败: %s", exc)
        success = False
        detail = f"{type(exc).__name__}: {exc}"
        actual = False

    verified = bool(success and actual == target)
    if verified:
        return TrayActionResult(
            f"开机启动已{'启用' if target else '禁用'}",
            enabled=actual,
        )

    _logger.error(
        "开机启动%s失败: %s",
        "启用" if target else "禁用",
        detail or "状态校验失败",
    )
    return TrayActionResult(
        f"开机启动{'设置' if target else '取消'}失败，请查看日志",
        success=False,
        enabled=actual,
    )


def cleanup_music_cache(cache_root: Path | None = None) -> TrayActionResult:
    """Delete music cache files without touching history or login data."""
    root = Path(cache_root) if cache_root is not None else get_user_cache_dir("music")
    platform_dirs = [root / name for name in _MUSIC_CACHE_PLATFORMS if (root / name).is_dir()]
    deleted_files = 0
    failed_files = 0
    deleted_bytes = 0

    for platform_dir in platform_dirs:
        for file_path in platform_dir.rglob("*"):
            if not file_path.is_file():
                continue
            try:
                file_size = file_path.stat().st_size
            except OSError:
                file_size = 0
            try:
                file_path.unlink()
                deleted_files += 1
                deleted_bytes += max(0, file_size)
            except OSError as exc:
                failed_files += 1
                _logger.warning("清理缓存失败: %s (%s)", file_path, exc)

    if deleted_files == 0 and failed_files == 0:
        message = "现在很干净，无需清理缓存"
    elif deleted_files > 0:
        message = f"已清理 {deleted_bytes / (1024 * 1024):.2f} MB 缓存"
        if failed_files > 0:
            message += f"（{failed_files} 项清理失败）"
    else:
        message = f"缓存清理失败，{failed_files} 项被占用"
    return TrayActionResult(message, success=failed_files == 0)


def cleanup_music_history() -> TrayActionResult:
    """Clear all music history and login data without touching cache files."""
    try:
        from lib.script.music import clear_all_history_and_login_data

        result = clear_all_history_and_login_data()
        history_items = int(result.get("history_items") or 0)
        deleted_login_files = int(result.get("deleted_login_files") or 0)
        logged_in_providers = int(result.get("logged_in_providers") or 0)
        total_failed = (
            int(result.get("history_failures") or 0)
            + int(result.get("failed_login_files") or 0)
            + int(result.get("login_provider_failures") or 0)
        )
        cleared_login = deleted_login_files > 0 or logged_in_providers > 0
        if history_items == 0 and not cleared_login and total_failed == 0:
            message = "暂无音乐历史或登录数据需要清理"
        else:
            parts: list[str] = []
            if history_items > 0:
                parts.append(f"已清空 {history_items} 条音乐历史")
            if cleared_login:
                parts.append("已清除登录数据")
            message = "，".join(parts) if parts else "音乐历史与登录数据已清理"
            if total_failed > 0:
                message += f"（{total_failed} 项清理失败）"
        return TrayActionResult(message, success=total_failed == 0)
    except Exception as exc:
        _logger.error("清理音乐历史与登录数据失败: %s", exc)
        return TrayActionResult("清理历史失败，请查看日志", success=False)


def open_author_page(
    opener: Callable[[str], bool] = webbrowser.open,
) -> TrayActionResult:
    """Open the author's public page using the configured system browser."""
    try:
        opened = bool(opener(AUTHOR_PAGE_URL))
    except Exception as exc:
        _logger.warning("打开作者主页失败: %s", exc)
        opened = False
    return TrayActionResult(
        "已打开作者主页" if opened else "打开作者主页失败",
        success=opened,
    )


__all__ = [
    "AUTHOR_PAGE_URL",
    "TrayActionResult",
    "cleanup_music_cache",
    "cleanup_music_history",
    "open_author_page",
    "prepare_autostart_state",
    "set_autostart_enabled",
]
