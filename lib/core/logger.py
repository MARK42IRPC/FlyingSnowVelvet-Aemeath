"""应用日志初始化与清理。"""

from __future__ import annotations

import glob
import logging
import os
import sys
import threading
from datetime import datetime

_FMT = '[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(name)-20s] %(message)s'
_DATEFMT = '%Y-%m-%d %H:%M:%S'
_ROOT_NAME = 'app'
MAX_LOG_FILES = 5

_initialized = False
_lock = threading.Lock()

_ANSI_RESET = '\033[0m'
_COLOR_MAP = {
    logging.DEBUG: '\033[90m',      # Gray
    logging.INFO: '\033[37m',       # White
    logging.WARNING: '\033[33m',    # Yellow
    logging.ERROR: '\033[31m',      # Red
    logging.CRITICAL: '\033[41m',   # Red background
}


class _AnsiFormatter(logging.Formatter):
    def __init__(self, *args, enable_color: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._enable_color = enable_color

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not self._enable_color:
            return message
        color = _COLOR_MAP.get(record.levelno)
        if not color:
            return message
        return f'{color}{message}{_ANSI_RESET}'


class _ErrorEventHandler(logging.Handler):
    """Publish an internal event for red ERROR/CRITICAL log records."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        if getattr(self._local, 'emitting', False):
            return
        self._local.emitting = True
        try:
            from lib.core.event.center import Event, EventType, get_event_center

            get_event_center().publish(Event(EventType.LOG_ERROR, {
                'logger': record.name,
                'level': record.levelname,
                'levelno': record.levelno,
                'message': record.getMessage(),
            }))
        except Exception:
            pass
        finally:
            self._local.emitting = False


def _enable_windows_ansi(stream) -> bool:
    if os.name != 'nt':
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & 0x0004:  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _supports_color(stream) -> bool:
    if stream is None or not hasattr(stream, 'isatty'):
        return False
    if not stream.isatty():
        return False
    if os.name == 'nt':
        return _enable_windows_ansi(stream)
    return True


def initialize(project_root: str) -> None:
    """初始化根日志器并创建当次运行日志文件。"""
    global _initialized
    with _lock:
        if _initialized:
            return

        log_dir = os.path.join(project_root, 'logs')
        os.makedirs(log_dir, exist_ok=True)
        _cleanup_old_logs(log_dir)

        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
        log_path = os.path.join(log_dir, f'app_{ts}.log')

        console_handler = logging.StreamHandler()
        color_enabled = _supports_color(console_handler.stream)
        console_formatter = _AnsiFormatter(_FMT, datefmt=_DATEFMT, enable_color=color_enabled)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.DEBUG)

        file_handler = logging.FileHandler(log_path, encoding='utf-8', delay=False)
        file_handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
        file_handler.setLevel(logging.DEBUG)

        error_event_handler = _ErrorEventHandler()

        root = logging.getLogger(_ROOT_NAME)
        root.setLevel(logging.DEBUG)
        root.handlers.clear()
        root.addHandler(console_handler)
        root.addHandler(file_handler)
        root.addHandler(error_event_handler)
        root.propagate = False

        _initialized = True
        root.info('[AppLogger] 日志文件已创建: %s', log_path)


def cleanup() -> None:
    """关闭并释放所有日志处理器。"""
    global _initialized
    root = logging.getLogger(_ROOT_NAME)
    if root.handlers:
        root.info('[AppLogger] 日志系统已关闭')
    for handler in root.handlers[:]:
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
        root.removeHandler(handler)
    _initialized = False


def get_logger(name: str) -> logging.Logger:
    """返回挂在 `app` 根命名空间下的子 logger。"""
    child_name = name if name.startswith(_ROOT_NAME + '.') else f'{_ROOT_NAME}.{name}'
    return logging.getLogger(child_name)


def _cleanup_old_logs(log_dir: str) -> None:
    pattern = os.path.join(log_dir, 'app_*.log')
    log_files = sorted(glob.glob(pattern), key=os.path.getmtime)
    quota = MAX_LOG_FILES - 1
    for file_path in log_files[:max(0, len(log_files) - quota)]:
        try:
            os.remove(file_path)
        except OSError:
            pass
