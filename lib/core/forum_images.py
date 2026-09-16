"""雪绒社区的图片字节缓存：列表缩略图与正文配图共用同一份。

图片是**不可变**的：id 就是内容地址，服务端给的是 `Cache-Control: immutable`，所以取回来
就能一直用。落盘位置与浏览快照同一个缓存根（`<用户根>/cache/forum/images/`，见
`lib/core/forum_cache.py`），于是桌宠每次启动的自动清理与账号页的「清理浏览缓存」都顺手
把它清掉，不需要另一套入口。

容量单独一套：单个文件 ≤2 MiB，总量 ≤32 MiB，超了按修改时间丢最早的。单个文件的上限比
服务端的 1.5 MB 松一点——真收下来才发现超了也没必要留在盘上。

模块不导入任何 GUI：字节进出，解码成图是界面层的事。
"""

from __future__ import annotations

import os
from pathlib import Path
import re

from lib.core.forum_cache import cache_dir
from lib.core.logger import get_logger

_logger = get_logger(__name__)

#: 单个文件与整个图片目录的上限。
FORUM_IMAGE_CACHE_MAX_ENTRY_BYTES = 2 * 1024 * 1024
FORUM_IMAGE_CACHE_MAX_TOTAL_BYTES = 32 * 1024 * 1024
#: 图片 id 是 32 位十六进制；只留这些字符，拼不出目录穿越的路径。
_ID_RE = re.compile(r"[^0-9a-zA-Z_-]+")


def images_dir() -> Path:
    """图片缓存目录；与浏览快照同根，所以清理逻辑不用改也覆盖得到。"""
    return cache_dir() / "images"


def _safe_id(image_id) -> str:
    cleaned = _ID_RE.sub("", str(image_id or "").strip())
    return cleaned[:64]


def image_path(image_id) -> Path:
    return images_dir() / _safe_id(image_id)


def read_image_bytes(image_id) -> bytes | None:
    """读一张已缓存的图；没有缓存、id 为空或读失败都返回 None。"""
    ident = _safe_id(image_id)
    if not ident:
        return None
    try:
        data = image_path(ident).read_bytes()
    except OSError:
        return None
    except Exception as exc:
        _logger.debug("[ForumImages] 读缓存失败 %s: %s", ident, exc)
        return None
    return data or None


def write_image_bytes(image_id, data) -> bool:
    """把一张图落盘；空的、超单条上限或写不进去都返回 False（缓存失败不影响显示）。"""
    ident = _safe_id(image_id)
    raw = bytes(data or b"")
    if not ident or not raw or len(raw) > FORUM_IMAGE_CACHE_MAX_ENTRY_BYTES:
        return False
    path = image_path(ident)
    temp = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp, "wb") as handle:
            handle.write(raw)
        os.replace(temp, path)
    except OSError as exc:
        _logger.debug("[ForumImages] 写缓存失败 %s: %s", path, exc)
        try:
            temp.unlink()
        except OSError:
            pass
        return False
    trim_images()
    return True


def image_stats() -> dict:
    """当前占用：文件数与总字节数。"""
    files = 0
    total = 0
    for path in _image_files():
        try:
            files += 1
            total += path.stat().st_size
        except OSError:
            continue
    return {"files": files, "bytes": total}


def trim_images() -> None:
    """总量超过上限时按修改时间丢最早的图片。"""
    entries: list[tuple[Path, int]] = []
    total = 0
    for path in _image_files():
        try:
            size = path.stat().st_size
        except OSError:
            continue
        entries.append((path, size))
        total += size
    if total <= FORUM_IMAGE_CACHE_MAX_TOTAL_BYTES:
        return
    for path, size in entries:
        if total <= FORUM_IMAGE_CACHE_MAX_TOTAL_BYTES:
            break
        try:
            path.unlink()
            total -= size
        except OSError:
            continue


def _image_files() -> list[Path]:
    root = images_dir()
    try:
        return sorted(
            (path for path in root.iterdir() if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
    except OSError:
        return []


__all__ = [
    "FORUM_IMAGE_CACHE_MAX_ENTRY_BYTES",
    "FORUM_IMAGE_CACHE_MAX_TOTAL_BYTES",
    "image_path",
    "image_stats",
    "images_dir",
    "read_image_bytes",
    "trim_images",
    "write_image_bytes",
]
