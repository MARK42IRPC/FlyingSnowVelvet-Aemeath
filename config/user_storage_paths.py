"""Canonical paths for user-owned settings, state, secrets, and caches."""

from __future__ import annotations

from pathlib import Path

from config.shared_storage_paths import get_shared_root_dir
from lib.core.secret_files import harden_secret_path


def get_user_root_dir() -> Path:
    return get_shared_root_dir() / "user"


def get_user_settings_path() -> Path:
    return get_user_root_dir() / "settings.json"


def get_user_persona_path() -> Path:
    return get_user_root_dir() / "persona.txt"


def get_user_secrets_dir(*parts: str) -> Path:
    return get_user_root_dir().joinpath("secrets", *parts)


def get_user_state_dir(*parts: str) -> Path:
    return get_user_root_dir().joinpath("state", *parts)


def get_user_cache_dir(*parts: str) -> Path:
    return get_shared_root_dir().joinpath("cache", *parts)


def get_user_logs_dir(*parts: str) -> Path:
    return get_shared_root_dir().joinpath("logs", *parts)


def ensure_user_storage_layout() -> None:
    for path in (
        get_user_root_dir(),
        get_user_secrets_dir(),
        get_user_state_dir(),
        get_user_cache_dir(),
        get_user_logs_dir(),
    ):
        path.mkdir(parents=True, exist_ok=True)
    # secrets 目录单独收紧：里面的 token / 密钥 / 登录 Cookie 不应被同机其它账户读到，
    # 收紧目录自身的 ACL 后，之后新建的文件与子目录都会继承这套权限。
    harden_secret_path(get_user_secrets_dir())
