"""Per-package local state for the official Lahai Tetris package."""

from __future__ import annotations

import json
import threading
from pathlib import Path


class LahaiTetrisStats:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._best_score = 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        try:
            value = max(0, int(data.get("best_score", 0)))
        except (TypeError, ValueError):
            value = 0
        with self._lock:
            self._best_score = value

    def _save(self) -> None:
        payload = {"best_score": int(self._best_score)}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_best_score(self) -> int:
        with self._lock:
            return int(self._best_score)

    def update_best_score(self, score: int) -> bool:
        try:
            value = max(0, int(score))
        except (TypeError, ValueError):
            return False
        with self._lock:
            if value <= self._best_score:
                return False
            self._best_score = value
        self._save()
        return True
