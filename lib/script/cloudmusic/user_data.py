"""Qt-free music history and login-data cleanup helpers.

The tray can request cleanup before the playback runtime has ever been
created.  Keeping this path separate prevents a data-only action from
importing the Qt multimedia player or starting a login worker.
"""
from __future__ import annotations

from config.music import get_music_history
from lib.core.logger import get_logger

from ._constants import (
    _KUGOU_LOGIN_CACHE_FILE,
    _LEGACY_LOGIN_CACHE_FILE,
    _LOGIN_CACHE_FILE,
    _QQ_LOGIN_CACHE_FILE,
    ensure_user_storage_layout,
)
from ._provider_clients import get_kugou_provider_client, get_qqmusic_provider_client


logger = get_logger(__name__)
HISTORY_CLEAR_PROVIDERS = ("netease", "qq", "kugou", "local", "other")
KNOWN_LOGIN_PROVIDERS = ("netease", "qq", "kugou")


def _iter_login_cache_files():
    files = [
        _LOGIN_CACHE_FILE,
        _QQ_LOGIN_CACHE_FILE,
        _KUGOU_LOGIN_CACHE_FILE,
        _LEGACY_LOGIN_CACHE_FILE,
    ]
    try:
        from config.shared_storage import get_shared_config_path

        files.extend(
            [
                get_shared_config_path("music", "cloudmusic_login_cache.json"),
                get_shared_config_path("music", "qqmusic_login_cache.json"),
                get_shared_config_path("music", "kugou_login_cache.json"),
            ]
        )
    except Exception:
        pass

    unique_files = []
    seen: set[str] = set()
    for file_path in files:
        try:
            key = str(file_path.resolve())
        except Exception:
            key = str(file_path)
        if key in seen:
            continue
        seen.add(key)
        unique_files.append(file_path)
    return unique_files


def _clear_runtime_netease_login_cookies() -> bool:
    try:
        from pyncm.apis.login import GetCurrentSession
    except Exception:
        return False

    try:
        session = GetCurrentSession()
    except Exception as exc:
        logger.debug("[CloudMusic] 获取当前会话失败，无法清理网易 Cookie: %s", exc)
        return False

    jar = getattr(session, "cookies", None)
    if jar is None:
        return False

    try:
        jar.clear()
        return True
    except Exception:
        pass

    cleared = False
    try:
        for cookie in list(jar):
            try:
                jar.clear(domain=cookie.domain, path=cookie.path, name=cookie.name)
                cleared = True
            except Exception:
                continue
    except Exception as exc:
        logger.debug("[CloudMusic] 网易 Cookie 逐项清理失败: %s", exc)
    return cleared


def clear_music_history_data() -> dict[str, int]:
    """Clear stored history without constructing the playback manager."""
    stats = {
        "history_items": 0,
        "history_platforms": 0,
        "history_failures": 0,
    }
    for provider in HISTORY_CLEAR_PROVIDERS:
        try:
            history = get_music_history(provider)
            stats["history_items"] += len(history.get_all())
            history.clear()
            stats["history_platforms"] += 1
        except Exception as exc:
            stats["history_failures"] += 1
            logger.warning("[CloudMusic] 清理历史失败 provider=%s: %s", provider, exc)
    return stats


def clear_music_login_data(runtime_manager=None) -> dict[str, int]:
    """Clear login files and provider cookies, preserving runtime semantics."""
    stats = {
        "logged_in_providers": 0,
        "deleted_login_files": 0,
        "failed_login_files": 0,
        "login_provider_failures": 0,
    }

    if runtime_manager is not None:
        stats["logged_in_providers"] = sum(
            1
            for provider in KNOWN_LOGIN_PROVIDERS
            if runtime_manager.provider_logged_in(provider)
        )
        runtime_manager._qr_login_cancel.set()
        runtime_manager._publish_qr_hide()

    _clear_runtime_netease_login_cookies()

    for provider_name, getter in (
        ("qq", get_qqmusic_provider_client),
        ("kugou", get_kugou_provider_client),
    ):
        try:
            getter().set_cookies({})
        except Exception as exc:
            stats["login_provider_failures"] += 1
            logger.warning("[CloudMusic] 清理 %s 登录态失败: %s", provider_name, exc)

    for cache_file in _iter_login_cache_files():
        if not cache_file.exists():
            continue
        try:
            cache_file.unlink()
            stats["deleted_login_files"] += 1
        except OSError as exc:
            stats["failed_login_files"] += 1
            logger.warning("[CloudMusic] 清理登录缓存失败: %s (%s)", cache_file, exc)

    if runtime_manager is not None:
        try:
            runtime_manager._anonymous_login()
        except ImportError:
            runtime_manager._set_login_state(False, {}, provider="netease")
        except Exception as exc:
            logger.warning("[CloudMusic] 清理后回退匿名登录失败: %s", exc)
            runtime_manager._set_login_state(False, {}, provider="netease")

        with runtime_manager._state_lock:
            for provider in KNOWN_LOGIN_PROVIDERS:
                runtime_manager._login_states[provider] = {
                    "logged_in": False,
                    "profile": {},
                }
            runtime_manager._sync_current_login_state_locked()
        runtime_manager._publish_login_status()

    return stats


def clear_music_user_data(runtime_manager=None) -> dict[str, int]:
    """Clear history and login data without forcing a toolkit runtime."""
    ensure_user_storage_layout()
    stats = clear_music_history_data()
    stats.update(clear_music_login_data(runtime_manager=runtime_manager))
    return stats


__all__ = [
    "HISTORY_CLEAR_PROVIDERS",
    "KNOWN_LOGIN_PROVIDERS",
    "clear_music_history_data",
    "clear_music_login_data",
    "clear_music_user_data",
]
