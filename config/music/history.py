"""音乐历史记录缓存模块（按平台分文件存储）"""

from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Optional

from config.config import CLOUD_MUSIC
from config.shared_storage import ensure_shared_config_ready, get_shared_config_path
from config.user_storage_paths import get_user_state_dir
from lib.core.logger import get_logger

_logger = get_logger(__name__)

_HISTORY_FILE_PREFIX = "history"
_SUPPORTED_HISTORY_PROVIDERS = ("netease", "qq", "kugou", "local", "other")
_DURATION_PREFIX_RE = re.compile(r"^\s*(\d{1,3}):(\d{2})\s+(.*)$")
_ARTIST_SPLIT_RE = re.compile(r"\s*(?:、|/|,|&| feat\.?| FEAT\.?| ft\.?| FT\.?| x | X | ×)\s*")
_INT_TEXT_RE = re.compile(r"^-?\d+$")

_instances: dict[str, "MusicHistory"] = {}
_lock = threading.Lock()


def _project_root() -> Path:
    return Path(__file__).parent.parent.parent


def _normalize_provider_name(provider: str | None) -> str:
    raw = provider
    if raw is None:
        raw = str(CLOUD_MUSIC.get("provider", "netease") or "netease")
    normalized = str(raw).strip().lower() or "netease"
    if normalized not in _SUPPORTED_HISTORY_PROVIDERS:
        normalized = "netease"
    return normalized


def get_music_history(provider: str | None = None) -> "MusicHistory":
    """获取指定平台音乐历史记录单例。"""
    name = _normalize_provider_name(provider)
    inst = _instances.get(name)
    if inst is not None:
        return inst
    with _lock:
        inst = _instances.get(name)
        if inst is None:
            inst = MusicHistory(provider=name)
            _instances[name] = inst
        return inst


def cleanup_music_history(provider: str | None = None):
    """清理音乐历史记录单例。provider 为空时清理全部平台。"""
    with _lock:
        if provider is not None:
            name = _normalize_provider_name(provider)
            inst = _instances.pop(name, None)
            if inst is not None:
                inst.save()
            return
        for name, inst in list(_instances.items()):
            try:
                inst.save()
            finally:
                _instances.pop(name, None)


class MusicHistory:
    """音乐历史记录管理器（单平台）。"""

    def __init__(self, history_dir: Path | None = None, provider: str = "netease"):
        self._provider = _normalize_provider_name(provider)

        if history_dir is None:
            ensure_shared_config_ready()
            history_dir = get_user_state_dir("music", "history")

        self._history_dir = Path(history_dir)
        self._history_file = self._history_dir / f"{_HISTORY_FILE_PREFIX}_{self._provider}.json"
        self._legacy_history_files = (
            get_shared_config_path("music", "history", f"{_HISTORY_FILE_PREFIX}_{self._provider}.json"),
            _project_root() / "resc" / "user" / "history" / f"{_HISTORY_FILE_PREFIX}_{self._provider}.json",
        )
        self._history: list[dict[str, Any]] = []  # [{id, title, artist, duration_ms?}, ...]
        self._id_set: set[Any] = set()
        self._data_lock = threading.Lock()

        self._history_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_history_file()
        self._load()

    def _migrate_legacy_history_file(self) -> None:
        """迁移旧 history 文件到新路径（仅 netease）。"""
        if self._history_file.exists():
            return

        candidates = [
            *self._legacy_history_files,
            _project_root() / "resc" / "user" / "history.json",
            _project_root() / "config" / "music" / "history.json",
        ]
        for old in candidates:
            if not old.exists() or not old.is_file():
                continue
            try:
                old.replace(self._history_file)
                _logger.info("[MusicHistory] 旧历史文件已迁移: %s -> %s", old, self._history_file)
                return
            except OSError:
                try:
                    shutil.copy2(old, self._history_file)
                    _logger.info("[MusicHistory] 旧历史文件已复制迁移: %s -> %s", old, self._history_file)
                    return
                except OSError:
                    continue

    @staticmethod
    def _normalize_duration_ms(raw) -> Optional[int]:
        if raw is None:
            return None
        try:
            val = int(raw)
            return val if val >= 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_prefixed_duration(text: str) -> tuple[Optional[int], str]:
        m = _DURATION_PREFIX_RE.match(text or "")
        if not m:
            return None, text
        mins = int(m.group(1))
        secs = int(m.group(2))
        return (mins * 60 + secs) * 1000, (m.group(3) or "").strip()

    @staticmethod
    def _normalize_artist(artist: str) -> str:
        src = str(artist or "").strip()
        if not src:
            return ""
        parts = _ARTIST_SPLIT_RE.split(src)
        first = str(parts[0] if parts else src).strip()
        return first or src

    @staticmethod
    def _normalize_song_ref(raw) -> int | str | None:
        if raw is None or isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            if raw.is_integer():
                return int(raw)
            return str(raw)

        text = str(raw).strip()
        if not text:
            return None

        if text.lower().startswith("netease:"):
            sid = text.split(":", 1)[1].strip()
            if _INT_TEXT_RE.fullmatch(sid):
                return int(sid)
            return text

        if _INT_TEXT_RE.fullmatch(text):
            return int(text)
        return text

    def _normalize_history_entries(self, data: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        changed = False
        seen_ids: set[Any] = set()
        normalized: list[dict[str, Any]] = []

        for raw_item in data:
            if not isinstance(raw_item, dict):
                changed = True
                continue

            song_ref = self._normalize_song_ref(raw_item.get("id"))
            if song_ref is None:
                changed = True
                continue
            if song_ref in seen_ids:
                changed = True
                continue
            seen_ids.add(song_ref)

            title = str(raw_item.get("title") or "").strip()
            artist = str(raw_item.get("artist") or "").strip()
            duration_ms = self._normalize_duration_ms(raw_item.get("duration_ms"))

            pref_ms, stripped_title = self._parse_prefixed_duration(title)
            if pref_ms is not None:
                changed = True
                title = stripped_title
                if duration_ms is None:
                    duration_ms = pref_ms

            if " - " in title and not artist:
                left, right = title.split(" - ", 1)
                title = left.strip()
                artist = right.strip()
                changed = True

            first_artist = self._normalize_artist(artist)
            if first_artist != artist:
                artist = first_artist
                changed = True

            if not title:
                title = str(song_ref)
                changed = True

            item = {
                "id": song_ref,
                "title": title,
                "artist": artist,
            }
            if duration_ms is not None:
                item["duration_ms"] = duration_ms
            if item != raw_item:
                changed = True
            normalized.append(item)

        return normalized, changed

    def _load(self):
        """从文件加载历史记录。"""
        try:
            source = self._history_file
            if not source.exists():
                source = next((path for path in self._legacy_history_files if path.exists()), source)
            if source.exists():
                with open(source, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    normalized, changed = self._normalize_history_entries(data)
                    with self._data_lock:
                        self._history = normalized
                        self._id_set = {item.get("id") for item in normalized if item.get("id") is not None}
                    _logger.info("[MusicHistory] [%s] 已加载 %d 条历史记录", self._provider, len(self._history))
                    if changed or source != self._history_file:
                        self.save()
                        _logger.info("[MusicHistory] [%s] 已执行历史记录清洗迁移", self._provider)
        except (json.JSONDecodeError, OSError) as e:
            _logger.warning("[MusicHistory] [%s] 加载历史记录失败: %s", self._provider, e)
            with self._data_lock:
                self._history = []
                self._id_set = set()

    def save(self):
        """保存历史记录到文件。"""
        try:
            with self._data_lock:
                data = self._history.copy()
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            _logger.debug("[MusicHistory] [%s] 已保存 %d 条历史记录", self._provider, len(data))
        except OSError as e:
            _logger.error("[MusicHistory] [%s] 保存历史记录失败: %s", self._provider, e)

    def add(self, song_id, title: str = "", artist: str = "", duration_ms: Optional[int] = None) -> bool:
        """
        添加歌曲到历史记录。

        Returns:
            是否添加成功（已存在时返回 False）。
        """
        song_ref = self._normalize_song_ref(song_id)
        if song_ref is None:
            return False

        with self._data_lock:
            if song_ref in self._id_set:
                _logger.debug("[MusicHistory] [%s] 歌曲 %s 已存在，跳过", self._provider, song_ref)
                return False
            item = {
                "id": song_ref,
                "title": str(title or "").strip(),
                "artist": str(artist or "").strip(),
            }
            if duration_ms is not None:
                try:
                    item["duration_ms"] = int(duration_ms)
                except (TypeError, ValueError):
                    pass
            self._history.insert(0, item)
            self._id_set.add(song_ref)

        _logger.info("[MusicHistory] [%s] 已添加: %s - %s (%s)", self._provider, title, artist, song_ref)
        self.save()
        return True

    def exists(self, song_id) -> bool:
        """检查歌曲是否已在历史记录中。"""
        song_ref = self._normalize_song_ref(song_id)
        if song_ref is None:
            return False
        with self._data_lock:
            return song_ref in self._id_set

    def get_all(self) -> list[dict[str, Any]]:
        """获取所有历史记录。"""
        with self._data_lock:
            return self._history.copy()

    def get_recent(self, count: int = 10) -> list[dict[str, Any]]:
        """获取最近 N 条历史记录。"""
        with self._data_lock:
            return self._history[:count]

    def clear(self):
        """清空历史记录。"""
        with self._data_lock:
            self._history.clear()
            self._id_set.clear()
        self.save()
        _logger.info("[MusicHistory] [%s] 已清空历史记录", self._provider)

    def remove(self, song_id) -> bool:
        """
        从历史记录中删除指定歌曲。

        Returns:
            是否删除成功（未命中时返回 False）。
        """
        song_ref = self._normalize_song_ref(song_id)
        if song_ref is None:
            return False

        with self._data_lock:
            removed = False
            kept: list[dict[str, Any]] = []
            for item in self._history:
                item_ref = self._normalize_song_ref(item.get("id"))
                if item_ref == song_ref:
                    removed = True
                    continue
                kept.append(item)
            if not removed:
                return False
            self._history = kept
            self._id_set = {
                self._normalize_song_ref(item.get("id"))
                for item in kept
                if self._normalize_song_ref(item.get("id")) is not None
            }

        self.save()
        _logger.info("[MusicHistory] [%s] 已删除歌曲: %s", self._provider, song_ref)
        return True

    def count(self) -> int:
        """获取历史记录数量。"""
        with self._data_lock:
            return len(self._history)
