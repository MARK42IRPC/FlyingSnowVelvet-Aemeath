"""本机设备标识：一个跨启动稳定的八位十六进制短码。

留言墙用它给每条留言盖一个「同设备」标识（形如 ``sha-123abcDE``），让同一台机器发出的
留言可以被认成同一个人，同时不暴露任何可反查的硬件信息。设计约束：

  - **不导入 GUI**：模块只依赖标准库，可在任意线程调用；
  - **同一设备恒定**：优先读 Windows 注册表的 ``MachineGuid``（安装系统时生成，重装才会变），
    取不到时依次回退到主机名、MAC、用户目录，最后才用一个随机但持久化的值；
  - **不泄漏原文**：写进留言的只是 HMAC 之后的八位十六进制，原文不出本机；
  - **一次算出后缓存**：每台机器在同一个进程里只算一次。

标识的格式是 ``sha-`` 加八位十六进制（大写小写混排），与留言墙里 ``[sha-123abcDE]``
这类尾注一一对应。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import platform
import sys
import threading
import uuid

from lib.core.logger import get_logger

_logger = get_logger(__name__)

#: 标识前缀与总长度：``sha-`` + 8 位十六进制。
DEVICE_TAG_PREFIX = "sha-"
DEVICE_TAG_HEX_LENGTH = 8
#: HMAC 密钥：固定值即可，它不是安全边界，只是把硬件原文单向搅乱。
_SALT = b"flying-snow-velvet/device-tag/v1"

_lock = threading.Lock()
_cached: str | None = None


def _windows_machine_guid() -> str:
    """读取 Windows 的 MachineGuid；非 Windows 或读不到时返回空串。"""
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "MachineGuid")
        return str(value or "").strip()
    except Exception as exc:
        _logger.debug("读取 MachineGuid 失败，回退其它设备来源: %s", exc)
        return ""


def _fallback_seed() -> str:
    """MachineGuid 不可用时的回退来源，按稳定性从高到低拼接。"""
    parts = [
        platform.node(),
        str(uuid.getnode()),
        os.environ.get("COMPUTERNAME", ""),
        os.path.expanduser("~"),
        sys.platform,
    ]
    return "|".join(part for part in parts if part)


def _random_persistent_seed() -> str:
    """所有硬件来源都拿不到时，用持久化到用户根的随机值兜底。

    随机值写在 ``<用户根>/user/state/device_id``，同一份用户存储上的后续启动仍然一致。
    """
    try:
        from config.user_storage_paths import get_user_state_dir

        path = get_user_state_dir("device_id")
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
        generated = uuid.uuid4().hex
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated, encoding="utf-8")
        return generated
    except Exception as exc:
        _logger.debug("设备标识随机兜底写入失败: %s", exc)
        return uuid.uuid4().hex


def _compute_device_tag() -> str:
    seed = _windows_machine_guid() or _fallback_seed() or _random_persistent_seed()
    digest = hmac.new(_SALT, seed.encode("utf-8", "replace"), hashlib.sha256).hexdigest()
    # 大小写混排：前半小写、后半大写，肉眼更容易和随机串区分。
    body = digest[:DEVICE_TAG_HEX_LENGTH]
    mixed = "".join(
        char.upper() if index % 2 else char for index, char in enumerate(body)
    )
    return f"{DEVICE_TAG_PREFIX}{mixed}"


def get_device_tag() -> str:
    """返回本机设备标识（``sha-`` 加八位十六进制），同一进程内只算一次。"""
    global _cached
    with _lock:
        if _cached is None:
            try:
                _cached = _compute_device_tag()
            except Exception as exc:
                _logger.warning("设备标识计算失败，使用进程级随机值: %s", exc)
                _cached = f"{DEVICE_TAG_PREFIX}{uuid.uuid4().hex[:DEVICE_TAG_HEX_LENGTH]}"
        return _cached


def reset_device_tag_cache() -> None:
    """清掉缓存，仅供测试使用。"""
    global _cached
    with _lock:
        _cached = None


__all__ = [
    "DEVICE_TAG_HEX_LENGTH",
    "DEVICE_TAG_PREFIX",
    "get_device_tag",
    "reset_device_tag_cache",
]
