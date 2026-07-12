"""小游戏用户数据存储。"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Optional

from config.shared_storage import ensure_shared_config_ready
from config.shared_storage_paths import get_project_root, get_shared_root_dir
from config.user_storage_paths import get_user_state_dir
from lib.core.logger import get_logger

_logger = get_logger(__name__)

_STATS_FILE_NAME = "lahai_tetris.json"

_instance: Optional["GameUserStats"] = None
_lock = threading.Lock()


def get_game_user_stats() -> "GameUserStats":
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = GameUserStats()
    return _instance


def cleanup_game_user_stats() -> None:
    global _instance
    with _lock:
        if _instance is not None:
            _instance.save()
            _instance = None


class GameUserStats:
    """管理小游戏用户本地数据。"""

    def __init__(self) -> None:
        ensure_shared_config_ready()
        project_root = get_project_root()
        shared_root = get_shared_root_dir()
        self._shared_path = get_user_state_dir("games", _STATS_FILE_NAME)
        self._legacy_paths = (
            shared_root / "resc" / "user" / "games" / _STATS_FILE_NAME,
            project_root / "resc" / "user" / "games" / _STATS_FILE_NAME,
        )
        self._data_lock = threading.Lock()
        self._best_score = 0

        self._shared_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_file()
        self._load()

    def _migrate_legacy_file(self) -> None:
        if self._shared_path.exists():
            return
        for legacy_path in self._legacy_paths:
            if not legacy_path.exists():
                continue
            try:
                shutil.copy2(legacy_path, self._shared_path)
                return
            except OSError:
                continue

    def _load(self) -> None:
        try:
            source = self._shared_path
            if not source.exists():
                source = next((path for path in self._legacy_paths if path.exists()), source)
            if source.exists():
                data = json.loads(source.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    best_score = max(0, int(data.get("best_score", 0)))
                    with self._data_lock:
                        self._best_score = best_score
                    if source != self._shared_path:
                        self.save()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            _logger.warning("[GameUserStats] 加载拉海洛方块最高分失败: %s", exc)
            with self._data_lock:
                self._best_score = 0

    def save(self) -> None:
        try:
            with self._data_lock:
                payload = {"best_score": int(self._best_score)}
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            self._shared_path.parent.mkdir(parents=True, exist_ok=True)
            self._shared_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            _logger.error("[GameUserStats] 保存拉海洛方块最高分失败: %s", exc)

    def get_best_score(self) -> int:
        with self._data_lock:
            return int(self._best_score)

    def update_best_score(self, score: int) -> bool:
        try:
            value = max(0, int(score))
        except (TypeError, ValueError):
            return False
        with self._data_lock:
            if value <= self._best_score:
                return False
            self._best_score = value
        self.save()
        return True
