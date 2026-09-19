"""Legacy shared-storage compatibility helpers.

New user-owned files live under ``C:\\AemeathDeskPet\\user``. The old
``C:\\AemeathDeskPet\\config`` tree is retained as a read-only migration source.
"""

from __future__ import annotations

import threading

from lib.core.logger import get_logger
from config.shared_storage_paths import (
    get_project_root,
    get_shared_config_path,
    get_shared_root_dir,
    resolve_shared_config_path as _resolve_shared_config_path,
)
from config.shared_storage_io import flush_pending_syncs as _flush_pending_syncs
from config.user_storage_paths import ensure_user_storage_layout

#: 旧调用方一直从本模块取这两个路径助手（config.music.*、lib.script.chat.memory 等），
#: 保持再导出；真正实现留在 config.shared_storage_paths。
__all__ = ["ensure_shared_config_ready", "get_project_root", "get_shared_config_path"]

_logger = get_logger(__name__)

_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAPPED = False

def ensure_shared_config_ready() -> None:
    """Ensure canonical user storage exists without mirroring source config."""
    global _BOOTSTRAPPED
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return

        shared_cfg = _resolve_shared_config_path()
        ensure_user_storage_layout()
        _flush_pending_syncs(shared_cfg)

        _BOOTSTRAPPED = True
        _logger.info('[UserStorage] 用户存储目录已就绪: %s', get_shared_root_dir())


def mirror_project_config_file_to_shared(rel_name: str) -> None:
    """Deprecated no-op kept for third-party compatibility."""
    _logger.warning('[UserStorage] 已忽略旧配置镜像请求: %s', rel_name)
