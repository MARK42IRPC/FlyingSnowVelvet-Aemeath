"""Bug tracker launcher and singleton process manager."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from config.user_storage_paths import get_user_logs_dir
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger
from lib.script.bug_tracker.storage import get_bug_tracker_event_log_path

logger = get_logger(__name__)

_IPC_HOST = "127.0.0.1"
_IPC_PORT = 49673
_STARTUP_WAIT_SECS = 6.0


def _hidden_console_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    kwargs: dict[str, object] = {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creationflags:
        kwargs["creationflags"] = creationflags
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is not None:
        startupinfo = startupinfo_cls()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ipc_port() -> int:
    return _IPC_PORT


def _send_command(command: str, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((_IPC_HOST, _ipc_port()), timeout=timeout) as conn:
            conn.sendall(command.encode("utf-8"))
            return True
    except OSError:
        return False


class BugTrackerService:
    def __init__(self) -> None:
        self._ec = get_event_center()
        self._ec.subscribe(EventType.BUG_TRACKER_OPEN_REQUEST, self._on_open_request)
        self._proc_lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._started_by_app = False
        self._ipc_ready = False
        self._startup_in_progress = False
        self._stopping = False

    def _on_open_request(self, event: Event) -> None:
        del event
        self.launch_or_focus()

    def launch_or_focus(self) -> bool:
        with self._proc_lock:
            if self._stopping:
                return False
            if self._startup_in_progress:
                return True
            proc = self._process
            if proc is not None and proc.poll() is None:
                worker = self._send_show_command
            else:
                self._startup_in_progress = True
                worker = self._ensure_process

        threading.Thread(
            target=worker,
            name='bug-tracker-launch',
            daemon=True,
        ).start()
        return True

    def _send_show_command(self) -> None:
        if _send_command('SHOW'):
            with self._proc_lock:
                self._ipc_ready = True

    def _ensure_process(self) -> None:
        if _send_command('SHOW'):
            self._mark_startup_finished(ipc_ready=True)
            return

        with self._proc_lock:
            if self._stopping:
                self._startup_in_progress = False
                return
            proc = self._process
            if proc is not None and proc.poll() is None:
                pass
            else:
                proc = None

        if proc is None:
            env = os.environ.copy()
            env['BUG_TRACKER_IPC_PORT'] = str(_ipc_port())
            env['BUG_TRACKER_EVENT_LOG'] = str(get_bug_tracker_event_log_path())
            env['PYTHONIOENCODING'] = 'utf-8'
            env.setdefault('PYTHONUNBUFFERED', '1')

            cmd = [sys.executable, '-m', 'lib.script.bug_tracker']
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(_project_root()),
                    env=env,
                    **_hidden_console_kwargs(),
                )
                self._set_tracked_process(proc)
            except Exception as exc:
                self._mark_startup_finished()
                self._publish_startup_failure(f'启动 bug 跟踪器失败: {exc}')
                return

        self._wait_for_ipc(proc)

    def _wait_for_ipc(self, proc: subprocess.Popen) -> None:
        deadline = time.monotonic() + _STARTUP_WAIT_SECS
        while time.monotonic() < deadline:
            with self._proc_lock:
                if self._stopping:
                    self._startup_in_progress = False
                    return
            if _send_command('SHOW', timeout=0.4):
                self._mark_startup_finished(ipc_ready=True)
                return
            if proc.poll() is not None:
                break
            time.sleep(0.2)

        if proc.poll() is not None:
            self._clear_tracked_process(proc)
            self._mark_startup_finished()
            logger.error('bug tracker exited before IPC became ready: %s', proc.returncode)
            self._publish_startup_failure('bug 跟踪器启动失败，请检查日志。')
            return

        self._mark_startup_finished()
        self._ec.publish(Event(EventType.INFORMATION, {
            'text': 'bug 跟踪器已启动，但 IPC 尚未就绪。',
            'min': 8,
            'max': 160,
        }))

    def _mark_startup_finished(self, *, ipc_ready: bool = False) -> None:
        with self._proc_lock:
            self._startup_in_progress = False
            self._ipc_ready = ipc_ready

    def _publish_startup_failure(self, text: str) -> None:
        self._ec.publish(Event(EventType.INFORMATION, {
            'text': text,
            'min': 12,
            'max': 180,
        }))

    def _tracked_process(self) -> Optional[subprocess.Popen]:
        with self._proc_lock:
            return self._process

    def _set_tracked_process(self, proc: subprocess.Popen) -> None:
        with self._proc_lock:
            self._process = proc
            self._started_by_app = True
            self._ipc_ready = False

    def _clear_tracked_process(self, expected: Optional[subprocess.Popen] = None) -> None:
        with self._proc_lock:
            if expected is not None and self._process is not expected:
                return
            self._process = None
            self._started_by_app = False
            self._ipc_ready = False

    def cleanup(self) -> None:
        with self._proc_lock:
            self._stopping = True
        self._ec.unsubscribe(EventType.BUG_TRACKER_OPEN_REQUEST, self._on_open_request)
        proc, started = self._take_tracked_process()
        if started and proc is not None:
            self._shutdown_process(proc)

    def _take_tracked_process(self) -> tuple[Optional[subprocess.Popen], bool]:
        with self._proc_lock:
            proc = self._process
            started = self._started_by_app
            self._process = None
            self._started_by_app = False
        return proc, started

    def _shutdown_process(self, proc: subprocess.Popen) -> None:
        try:
            _send_command("QUIT")
        except Exception:
            pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        try:
            proc.terminate()
        except Exception:
            pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        try:
            proc.kill()
        except Exception:
            pass


_instance: Optional[BugTrackerService] = None


def get_bug_tracker_service() -> BugTrackerService:
    global _instance
    if _instance is None:
        _instance = BugTrackerService()
    return _instance


def cleanup_bug_tracker_service() -> None:
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None


def get_bug_tracker_ipc_port() -> int:
    return _ipc_port()


def get_bug_tracker_ipc_host() -> str:
    return _IPC_HOST
