"""Persistent streak record in ``<data_root>/state.json`` (pure Python)."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from .betting import StreakState

VERSION = 1


class DangoStats:
    """Best-effort persistent record: corrupt files fall back to defaults."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._streak = StreakState()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        try:
            version = int(data.get("version", 1))
        except (TypeError, ValueError, OverflowError):
            return
        if version != VERSION:
            return
        self._streak = StreakState.from_record(data)

    def save(self) -> None:
        record = {"version": VERSION, **self._streak.as_record(), "last_updated": datetime.now().isoformat(timespec="seconds")}
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return self._streak.as_record()

    def settle(self, *, bet_side: str, winner: str, double_ko: bool, amount: int) -> bool:
        with self._lock:
            won = self._streak.settle(bet_side=bet_side, winner=winner, double_ko=double_ko, amount=amount)
        self.save()
        return won

    def reset_current(self) -> None:
        with self._lock:
            self._streak.current = 0
        self.save()
