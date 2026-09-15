"""更新流程里比较路径时的写法归一。

Windows 上同一个目录可以有多种写法：``C:\\Users\\RUNNER~1\\...`` 是 8.3 短名，
junction 与符号链接又指到别处，``Path.resolve()`` 会把它们一律换成真实路径；而进程镜像
路径来自 ``QueryFullProcessImageNameW``，是启动时原样给出的字符串。于是「解析过的安装
根」与「没解析的镜像路径」直接比较必然对不上：占用进程一个都挑不出来（覆盖照样报 13 号
错误），补装清单也会被判成属于另一个安装目录而被丢掉。CI 上仓库与 TEMP 都在 ``D:\\a``
这类短名 / 链接路径下，正好把这个问题暴露出来。

这里只统一「比较用的写法」：``realpath`` 摊平短名与 junction，再按 Windows 不区分大小写
的规则统一大小写。返回值只用于比较，不要拿去读写文件。
"""

from __future__ import annotations

import os
from pathlib import Path


def canonical_path(path: Path | str) -> str:
    """返回只用于比较的规范路径。"""
    raw = os.fspath(path)
    try:
        resolved = os.path.realpath(raw)
    except OSError:  # pragma: no cover - realpath 基本不抛，这里只是兜底
        resolved = os.path.abspath(raw)
    return os.path.normcase(resolved)


def is_inside(path: Path | str | None, root: Path | str) -> bool:
    """``path`` 是否位于 ``root`` 之下（``root`` 自己不算）。"""
    if not path:
        return False
    prefix = canonical_path(root).rstrip(os.sep) + os.sep
    return canonical_path(path).startswith(prefix)


__all__ = ["canonical_path", "is_inside"]
