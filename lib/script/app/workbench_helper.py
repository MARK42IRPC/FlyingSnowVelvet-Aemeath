"""Launch the optional Qt workbench in a separate process."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from config.user_storage_paths import get_user_state_dir


_HELPER_REQUEST_VERSION = 2
_HELPER_LOCK = threading.RLock()
_helper_process: subprocess.Popen | None = None


def normalize_workbench_page(page_id: object) -> str:
    value = str(page_id or "").strip()
    if not value or len(value) > 64:
        return "overview"
    if not all(character.isalnum() or character in {"_", "-"} for character in value):
        return "overview"
    return value


def normalize_game_id(game_id: object) -> str:
    value = str(game_id or "").strip()
    if not value or len(value) > 64:
        return ""
    if not all(character.isalnum() or character in {"_", "-", "."} for character in value):
        return ""
    return value


def normalize_game_action(action: object) -> str:
    value = str(action or "").strip().lower()
    return value if value in {
        "open_manager",
        "close_manager",
        "open",
        "close",
    } else ""


def _helper_request_path() -> Path:
    return get_user_state_dir("workbench-helper", "request.json")


def _publish_helper_request(
    page_id: str,
    *,
    game_id: str = "",
    game_action: str = "",
) -> dict:
    path = _helper_request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _HELPER_REQUEST_VERSION,
        "request_id": uuid.uuid4().hex,
        "page_id": normalize_workbench_page(page_id),
        "game_id": normalize_game_id(game_id),
        "game_action": normalize_game_action(game_action),
    }
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    return payload


def read_workbench_helper_request() -> dict:
    try:
        payload = json.loads(_helper_request_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != _HELPER_REQUEST_VERSION:
        return {}
    request_id = str(payload.get("request_id") or "")
    if not request_id:
        return {}
    return {
        "version": _HELPER_REQUEST_VERSION,
        "request_id": request_id,
        "page_id": normalize_workbench_page(payload.get("page_id")),
        "game_id": normalize_game_id(payload.get("game_id")),
        "game_action": normalize_game_action(payload.get("game_action")),
    }


def launch_workbench_helper(
    initial_page: str = "overview",
    *,
    game_id: str = "",
    game_action: str = "",
) -> bool:
    global _helper_process

    page_id = normalize_workbench_page(initial_page)
    entry = Path(__file__).resolve().parents[2] / 'core' / 'qt_desktop_pet.py'
    command = [
        sys.executable,
        str(entry),
        '--fsv-workbench-helper',
        '--initial-page',
        page_id,
    ]
    kwargs = {
        'cwd': str(entry.parents[2]),
        'stdin': subprocess.DEVNULL,
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
        'close_fds': True,
    }
    if os.name == 'nt':
        kwargs['creationflags'] = (
            getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            | getattr(subprocess, 'DETACHED_PROCESS', 0)
        )
    else:
        kwargs['start_new_session'] = True
    with _HELPER_LOCK:
        try:
            _publish_helper_request(
                page_id,
                game_id=game_id,
                game_action=game_action,
            )
        except OSError:
            return False
        process = _helper_process
        if process is not None and process.poll() is None:
            return True
        try:
            _helper_process = subprocess.Popen(command, **kwargs)
        except (OSError, subprocess.SubprocessError):
            _helper_process = None
            return False
        return True
