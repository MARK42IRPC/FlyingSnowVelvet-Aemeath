"""Console formatting shared by dependency installer stages."""

import os
import sys

from .catalog import TOTAL_STEPS


def _enable_ansi_color() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    if any(key in os.environ for key in ("ANSICON", "WT_SESSION", "TERM_PROGRAM")):
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            if mode.value & 0x0004:  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                return True
            if kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                return True
    except Exception:
        pass
    return False

_COLOR_ENABLED = _enable_ansi_color()

_COLOR_RESET = "\033[0m"

_COLOR_MAP = {
    "stage": "\033[95m",
    "info": "\033[96m",
    "ok": "\033[92m",
    "warn": "\033[93m",
    "error": "\033[91m",
    "progress_current": "\033[96m",
    "progress_overall": "\033[95m",
    "progress_track": "\033[90m",
    "progress_value": "\033[97m",
}

_LABELS = {
    "info": "[信息] ",
    "ok": "[完成] ",
    "warn": "[警告] ",
    "error": "[错误] ",
}

def _fmt_color(text: str, kind: str) -> str:
    if not _COLOR_ENABLED:
        return text
    code = _COLOR_MAP.get(kind)
    if not code:
        return text
    return f"{code}{text}{_COLOR_RESET}"

def _print_kind(text: str, kind: str = "info", *, prefix: bool = True) -> None:
    if prefix:
        text = f"{_LABELS.get(kind, '')}{text}"
    print(_fmt_color(text, kind))

def _print_info(text: str) -> None:
    _print_kind(text, "info")

def _print_warn(text: str) -> None:
    _print_kind(text, "warn")

def _print_error(text: str) -> None:
    _print_kind(text, "error")

def _print_stage(step: int, text: str) -> None:
    message = f"\n[{step}/{TOTAL_STEPS}] {text}"
    print(_fmt_color(message, "stage"))

__all__ = (
    '_enable_ansi_color',
    '_COLOR_ENABLED',
    '_COLOR_RESET',
    '_COLOR_MAP',
    '_LABELS',
    '_fmt_color',
    '_print_kind',
    '_print_info',
    '_print_warn',
    '_print_error',
    '_print_stage',
)
