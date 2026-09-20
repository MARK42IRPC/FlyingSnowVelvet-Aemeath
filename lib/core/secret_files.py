"""把密钥文件收紧到「仅当前用户（外加 SYSTEM / Administrators）可读写」。

`<用户根>/user/secrets/` 下放的是论坛 token、AI 密钥与各音乐平台的登录 Cookie。
它们默认继承共享根目录的权限，其中包含 `BUILTIN\\Users` 与
`NT AUTHORITY\\Authenticated Users`：同机上的其它账户可以直接读走这些凭据。
凡是要往 secrets 里落盘的调用方，写完后都应调用这里的 `harden_secret_path()`：

- Windows：写入一份受保护（不再继承）的 DACL，只保留当前用户、SYSTEM 与
  Administrators。目录额外打开 `OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE`，
  之后新建的文件与子目录会自动继承这套权限。保留 SYSTEM 与 Administrators 是刻意的：
  服务、备份与卸载程序仍能操作这些文件，而普通账户不属于这两个组。
- 非 Windows：`os.chmod` 到 `0o600`（文件）/ `0o700`（目录）。

收紧失败一律不抛异常：这是加固而非功能前置条件，拿不到权限句柄时记一条 debug 日志
继续走原来的写入结果即可。pywin32 缺失时同样降级为「不处理」。
"""

from __future__ import annotations

import os
from pathlib import Path

from lib.core.logger import get_logger

_logger = get_logger(__name__)


def harden_secret_path(path: Path) -> bool:
    """把 `path` 收紧为仅当前用户可访问，返回是否真的改成了目标权限。"""
    candidate = Path(path)
    try:
        is_dir = candidate.is_dir()
        if not candidate.exists():
            return False
    except OSError:
        return False

    try:
        if os.name == "nt":
            return _harden_windows(candidate, is_dir)
        return _harden_posix(candidate, is_dir)
    except Exception as exc:  # 加固属于尽力而为，任何意外都不该冒泡到调用方
        _logger.debug("[SecretFiles] 收紧权限失败 %s: %s", candidate, exc)
        return False


def _harden_posix(path: Path, is_dir: bool) -> bool:
    mode = 0o700 if is_dir else 0o600
    try:
        os.chmod(path, mode)
    except OSError as exc:
        _logger.debug("[SecretFiles] 收紧权限失败 %s: %s", path, exc)
        return False
    return True


def _harden_windows(path: Path, is_dir: bool) -> bool:
    try:
        import ntsecuritycon
        import win32api
        import win32security
    except ImportError as exc:
        _logger.debug("[SecretFiles] 缺少 pywin32，跳过权限收紧 %s: %s", path, exc)
        return False

    try:
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        try:
            owner_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            win32api.CloseHandle(token)
        system_sid = win32security.CreateWellKnownSid(
            win32security.WinLocalSystemSid, None
        )
        admins_sid = win32security.CreateWellKnownSid(
            win32security.WinBuiltinAdministratorsSid, None
        )

        inherit_flags = (
            win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
            if is_dir
            else 0
        )
        dacl = win32security.ACL()
        for sid in (owner_sid, system_sid, admins_sid):
            dacl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION,
                inherit_flags,
                ntsecuritycon.FILE_ALL_ACCESS,
                sid,
            )
        # PROTECTED 会丢掉从父目录继承来的 ACE，只留下上面这三条。
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
    except Exception as exc:  # pywin32 抛的是 win32security.error 等，统一降级
        _logger.debug("[SecretFiles] 收紧权限失败 %s: %s", path, exc)
        return False
    return True


__all__ = ["harden_secret_path"]
