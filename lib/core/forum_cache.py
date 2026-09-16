"""雪绒社区的浏览缓存：帖子列表 / 标签 / 账号资料的小快照。

社区页每次打开都要等一次网络，这里把「上次看到的内容」落成几百 KiB 的小 JSON，
下次打开先把快照铺上、再让请求结果覆盖它，观感是即开即有。

缓存是**可丢弃**的：桌宠每次启动清一次（`lib/script/app/startup_cleanup.py`），
账号页也有手动清理按钮，所以不会出现「一直看着旧内容」的长期风险。位置固定在
`<用户根>/cache/forum/`，与用户设置共享同一个可写根目录，覆盖安装不会动它。

缓存里只有公开内容（帖子标题、摘要、标签、显示名），**不写 token**：token 属于密钥，
落在 `<用户根>/user/secrets/forum/`，两者不混。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time

from config.user_storage_paths import get_user_cache_dir
from lib.core.logger import get_logger

_logger = get_logger(__name__)

#: 缓存格式版本；结构变化时自增，旧文件按版本不符直接丢弃。
FORUM_CACHE_VERSION = 1
#: 单个条目与大目录的上限：超出就丢最早的条目，缓存不能变成磁盘占用。
FORUM_CACHE_MAX_ENTRY_BYTES = 256 * 1024
FORUM_CACHE_MAX_TOTAL_BYTES = 1024 * 1024
_NAME_RE = re.compile(r"[^0-9a-z_-]+")


@dataclass(frozen=True, slots=True)
class ForumCacheReport:
    """一次清理的结果：删了几个文件、回收了多少字节、有没有失败。"""

    files: int = 0
    bytes: int = 0
    errors: tuple[str, ...] = ()


def cache_dir() -> Path:
    return get_user_cache_dir("forum")


def _safe_name(name: str) -> str:
    """条目名只保留小写字母、数字、下划线和横线，避免拼出目录穿越的路径。"""
    cleaned = _NAME_RE.sub("-", str(name or "").strip().lower()).strip("-")
    return cleaned[:64]


def entry_path(name: str) -> Path:
    return cache_dir() / f"{_safe_name(name)}.json"


def read_entry(name: str) -> dict | None:
    """读取一个缓存条目；文件缺失、损坏或版本不符都返回 None。"""
    path = entry_path(name)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    except Exception as exc:
        _logger.debug("[ForumCache] 读取缓存失败 %s: %s", path, exc)
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("version") != FORUM_CACHE_VERSION:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def write_entry(name: str, data) -> bool:
    """原子写入一个缓存条目；超过单条上限或写盘失败返回 False。"""
    if not isinstance(data, dict):
        return False
    payload = {
        "version": FORUM_CACHE_VERSION,
        "saved_at": int(time.time()),
        "data": data,
    }
    try:
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _logger.debug("[ForumCache] 缓存内容无法序列化: %s", exc)
        return False
    if len(blob) > FORUM_CACHE_MAX_ENTRY_BYTES:
        return False
    path = entry_path(name)
    temp = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp, "wb") as handle:
            handle.write(blob)
        os.replace(temp, path)
    except OSError as exc:
        _logger.debug("[ForumCache] 写入缓存失败 %s: %s", path, exc)
        try:
            temp.unlink()
        except OSError:
            pass
        return False
    _trim_total()
    return True


def drop_entry(name: str) -> bool:
    path = entry_path(name)
    try:
        path.unlink()
        return True
    except OSError:
        return False


def cache_stats() -> dict:
    """当前占用：文件数与总字节数，账号页把它显示在清理按钮旁边。

    图片目录也算在里面（它们同样长在缓存根下，清理按钮一起清），口径与
    `clear_cache()` 一致：清掉多少就说多少。
    """
    files = 0
    total = 0
    for path in _iter_cache_files():
        try:
            files += 1
            total += path.stat().st_size
        except OSError:
            continue
    return {"files": files, "bytes": total}


def _iter_cache_files() -> list[Path]:
    root = cache_dir()
    try:
        return sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
    except OSError:
        return []


def _trim_total() -> None:
    """总量超过上限时按修改时间丢最早的条目。

    只算 `*.json` 快照：图片字节另有自己的目录与预算（`lib/core/forum_images.py`），
    按快照这 1 MiB 的口径去数它们，随手写一张图就会把快照全挤掉。
    """
    total = 0
    files = [path for path in _iter_cache_files() if path.suffix == ".json"]
    sizes: list[tuple[Path, int]] = []
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        sizes.append((path, size))
        total += size
    if total <= FORUM_CACHE_MAX_TOTAL_BYTES:
        return
    for path, size in sizes:
        if total <= FORUM_CACHE_MAX_TOTAL_BYTES:
            break
        try:
            path.unlink()
            total -= size
        except OSError:
            continue


def clear_cache() -> ForumCacheReport:
    """整体清空缓存目录；返回删掉的文件数与字节数，失败项收集在 `errors` 里。"""
    files = 0
    freed = 0
    errors: list[str] = []
    for path in _iter_cache_files():
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        try:
            path.unlink()
            files += 1
            freed += size
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    _prune_dirs()
    return ForumCacheReport(files=files, bytes=freed, errors=tuple(errors))


def _prune_dirs() -> None:
    """顺手回收空目录，但保留缓存根目录本身。"""
    root = cache_dir()
    try:
        directories = sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
    except OSError:
        return
    for path in directories:
        try:
            path.rmdir()
        except OSError:
            continue


__all__ = [
    "FORUM_CACHE_MAX_ENTRY_BYTES",
    "FORUM_CACHE_MAX_TOTAL_BYTES",
    "FORUM_CACHE_VERSION",
    "ForumCacheReport",
    "cache_dir",
    "cache_stats",
    "clear_cache",
    "drop_entry",
    "entry_path",
    "read_entry",
    "write_entry",
]