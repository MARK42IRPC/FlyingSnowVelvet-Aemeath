"""One application and one temporary home per Qt child interpreter.

The legacy Qt tests each imported ``PyQt5`` at module scope and patched
``os.environ`` after some modules had already been imported.  That made the
process-wide state depend on discovery order.  Starting from this helper keeps
the environment decision in one place, before any product module is imported.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import PyQt5.QtCore
from PyQt5.QtWidgets import QApplication

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

_TEST_HOME = Path(tempfile.mkdtemp(prefix="tests-ng-home-"))
os.environ["AEMEATH_DESK_PET_HOME"] = str(_TEST_HOME)


def app() -> QApplication:
    """Return the only QApplication a child interpreter may have."""
    return QApplication.instance() or QApplication([])


def close_all_windows() -> None:
    """Close anything a test forgot to close before the interpreter exits."""
    for widget in QApplication.topLevelWidgets():
        try:
            widget.close()
        except RuntimeError:
            pass


def cleanup_home() -> None:
    shutil.rmtree(_TEST_HOME, ignore_errors=True)


__all__ = ["app", "cleanup_home", "close_all_windows"]
