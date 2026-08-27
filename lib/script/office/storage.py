"""Versioned task history for office-mode agents."""

from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from pathlib import Path

from config.user_storage_paths import get_user_state_dir

from .contracts import (
    ACTIVE_TASK_STATUSES,
    DEFAULT_REASONING_EFFORT,
    OfficeTaskStatus,
    normalize_reasoning_effort,
    task_title,
    utc_now_text,
)


TASK_STORE_VERSION = 1
MAX_TASK_HISTORY = 100
MAX_TASK_EVENTS = 500


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


class OfficeTaskStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or get_user_state_dir("office", "tasks.json"))
        self._lock = threading.RLock()
        self._tasks: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(payload, dict) or payload.get("version") != TASK_STORE_VERSION:
            return
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            return
        self._tasks = [copy.deepcopy(item) for item in tasks if isinstance(item, dict)][-MAX_TASK_HISTORY:]

    def _save_locked(self) -> None:
        _write_json_atomic(self.path, {
            "version": TASK_STORE_VERSION,
            "tasks": self._tasks[-MAX_TASK_HISTORY:],
        })

    def snapshot(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._tasks)

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._find_locked(task_id)
            return copy.deepcopy(task) if task is not None else None

    def active(self) -> dict | None:
        with self._lock:
            for task in reversed(self._tasks):
                if str(task.get("status")) in ACTIVE_TASK_STATUSES:
                    return copy.deepcopy(task)
        return None

    def latest(self) -> dict | None:
        """Return the task with the newest modification timestamp."""
        with self._lock:
            if not self._tasks:
                return None
            _index, task = max(
                enumerate(self._tasks),
                key=lambda item: (
                    str(item[1].get("updated_at") or item[1].get("created_at") or ""),
                    item[0],
                ),
            )
            return copy.deepcopy(task)

    def latest_resumable(self) -> dict | None:
        """Return the newest task that has a persisted DSH session."""
        with self._lock:
            candidates = [
                (index, task)
                for index, task in enumerate(self._tasks)
                if str(task.get("session_id") or "").strip()
            ]
            if not candidates:
                return None
            _index, task = max(
                candidates,
                key=lambda item: (
                    str(item[1].get("updated_at") or item[1].get("created_at") or ""),
                    item[0],
                ),
            )
            return copy.deepcopy(task)

    def delete(self, task_id: str) -> bool:
        """Remove one task from history and persist the updated snapshot."""
        value = str(task_id or "")
        if not value:
            return False
        with self._lock:
            for index, task in enumerate(self._tasks):
                if str(task.get("id") or "") != value:
                    continue
                del self._tasks[index]
                self._save_locked()
                return True
        return False

    def create(
        self,
        prompt: str,
        workspace: Path,
        *,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> dict:
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            raise ValueError("office task prompt cannot be empty")
        now = utc_now_text()
        task = {
            "id": uuid.uuid4().hex,
            "session_id": "",
            "title": task_title(normalized_prompt),
            "workspace": str(Path(workspace).resolve()),
            "status": OfficeTaskStatus.QUEUED.value,
            "reasoning_effort": normalize_reasoning_effort(reasoning_effort),
            "created_at": now,
            "updated_at": now,
            "messages": [{"role": "user", "text": normalized_prompt, "time": now}],
            "events": [],
            "todos": [],
            "stream_text": "",
            "reasoning_text": "",
            "error": "",
        }
        with self._lock:
            self._tasks.append(task)
            self._tasks = self._tasks[-MAX_TASK_HISTORY:]
            self._save_locked()
            return copy.deepcopy(task)

    def update(self, task_id: str, **changes) -> dict:
        with self._lock:
            task = self._require_locked(task_id)
            task.update(copy.deepcopy(changes))
            task["updated_at"] = utc_now_text()
            self._save_locked()
            return copy.deepcopy(task)

    def add_message(self, task_id: str, role: str, text: str) -> dict:
        normalized = str(text or "").strip()
        if not normalized:
            return self.get(task_id) or {}
        with self._lock:
            task = self._require_locked(task_id)
            task.setdefault("messages", []).append({
                "role": str(role or "system"),
                "text": normalized,
                "time": utc_now_text(),
            })
            task["updated_at"] = utc_now_text()
            self._save_locked()
            return copy.deepcopy(task)

    def add_event(self, task_id: str, event_type: str, data: dict | None = None) -> dict:
        with self._lock:
            task = self._require_locked(task_id)
            events = task.setdefault("events", [])
            events.append({
                "type": str(event_type or "event"),
                "data": copy.deepcopy(data or {}),
                "time": utc_now_text(),
            })
            if len(events) > MAX_TASK_EVENTS:
                del events[:-MAX_TASK_EVENTS]
            task["updated_at"] = utc_now_text()
            self._save_locked()
            return copy.deepcopy(task)

    def _find_locked(self, task_id: str) -> dict | None:
        value = str(task_id or "")
        return next((item for item in self._tasks if item.get("id") == value), None)

    def _require_locked(self, task_id: str) -> dict:
        task = self._find_locked(task_id)
        if task is None:
            raise KeyError(task_id)
        return task
