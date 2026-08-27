"""Atomic file IPC between the desktop process and the Qt workbench helper."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from config.user_storage_paths import get_user_state_dir


IPC_VERSION = 1


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


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


class OfficeFileIpc:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or get_user_state_dir("office"))
        self.state_path = self.root / "state.json"
        self.tasks_path = self.root / "tasks.json"
        self.commands_dir = self.root / "commands"
        self.commands_dir.mkdir(parents=True, exist_ok=True)

    def publish(self, state: dict, tasks: list[dict]) -> None:
        _write_json_atomic(self.state_path, {"version": IPC_VERSION, **state})
        _write_json_atomic(self.tasks_path, {"version": IPC_VERSION, "tasks": tasks})

    def read_state(self) -> dict:
        return _read_json(self.state_path) or {"version": IPC_VERSION}

    def read_tasks(self) -> list[dict]:
        payload = _read_json(self.tasks_path) or {}
        tasks = payload.get("tasks", [])
        return tasks if isinstance(tasks, list) else []

    def submit(self, command: str, **data) -> Path:
        payload = {
            "version": IPC_VERSION,
            "id": uuid.uuid4().hex,
            "command": str(command or "").strip(),
            "data": data,
        }
        if not payload["command"]:
            raise ValueError("office IPC command cannot be empty")
        path = self.commands_dir / f"{payload['id']}.json"
        _write_json_atomic(path, payload)
        return path

    def consume(self) -> list[dict]:
        commands: list[dict] = []
        for path in sorted(self.commands_dir.glob("*.json")):
            payload = _read_json(path)
            try:
                path.unlink()
            except OSError:
                continue
            if payload is None or payload.get("version") != IPC_VERSION:
                continue
            if not str(payload.get("command", "")).strip():
                continue
            commands.append(payload)
        return commands
