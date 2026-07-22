"""Bug tracker standalone application entry."""

from __future__ import annotations

import logging
import os
import socket
import threading
import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QObject, pyqtSignal

from config.user_storage_paths import get_user_logs_dir
from lib.script.bug_tracker.service import get_bug_tracker_ipc_host, get_bug_tracker_ipc_port
from lib.script.bug_tracker.window import BugTrackerWindow


def _setup_logging() -> None:
    log_path = get_user_logs_dir("bug_tracker.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("bug_tracker")
    root.setLevel(logging.INFO)
    if root.handlers:
        return
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    root.addHandler(handler)


class _Bridge(QObject):
    command_received = pyqtSignal(str)


class BugTrackerRuntime:
    def __init__(self, window: BugTrackerWindow) -> None:
        self._window = window
        self._bridge = _Bridge()
        self._bridge.command_received.connect(self._on_command)
        self._stop_event = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        host = get_bug_tracker_ipc_host()
        port = get_bug_tracker_ipc_port()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            sock.listen(4)
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            self._send_command("SHOW")
            return False
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, name="bug-tracker-ipc", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        self._poke()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def _poke(self) -> None:
        try:
            host = get_bug_tracker_ipc_host()
            port = get_bug_tracker_ipc_port()
            with socket.create_connection((host, port), timeout=0.2):
                pass
        except Exception:
            pass

    def _serve(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop_event.is_set():
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    data = conn.recv(1024)
                    command = data.decode("utf-8", errors="ignore").strip().upper()
                except Exception:
                    command = ""
                if command:
                    self._bridge.command_received.emit(command)
                try:
                    conn.sendall(b"OK")
                except Exception:
                    pass

    def _on_command(self, command: str) -> None:
        if command == "SHOW":
            self._window.show_centered()
            return
        if command == "REFRESH":
            self._window._refresh_now()
            return
        if command == "QUIT":
            self._window.close()
            QApplication.instance().quit()

    def _send_command(self, command: str) -> bool:
        host = get_bug_tracker_ipc_host()
        port = get_bug_tracker_ipc_port()
        try:
            with socket.create_connection((host, port), timeout=0.5) as conn:
                conn.sendall(command.encode("utf-8"))
                return True
        except OSError:
            return False


def main() -> int:
    _setup_logging()
    app = QApplication(sys.argv)
    window = BugTrackerWindow()
    runtime = BugTrackerRuntime(window)
    if not runtime.start():
        return 0
    app.aboutToQuit.connect(runtime.stop)
    window.show_centered()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
