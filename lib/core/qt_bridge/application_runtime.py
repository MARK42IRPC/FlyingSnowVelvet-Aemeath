"""Qt implementation of the application event-loop contract."""
from __future__ import annotations

import os
import sys
from typing import Callable

from PyQt5.QtCore import QEvent, QTimer, Qt
from PyQt5.QtWidgets import QApplication

from lib.core.application_runtime import ApplicationRuntime


def _ensure_qt_plugin_paths(logger: object) -> None:
    if "QT_QPA_PLATFORM_PLUGIN_PATH" in os.environ and "QT_PLUGIN_PATH" in os.environ:
        return

    try:
        import PyQt5.QtCore

        qt_path = os.path.dirname(PyQt5.QtCore.__file__)
        plugins_path = os.path.join(qt_path, "Qt5", "plugins")
        platforms_path = os.path.join(plugins_path, "platforms")
        if "QT_QPA_PLATFORM_PLUGIN_PATH" not in os.environ and os.path.exists(platforms_path):
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = platforms_path
            logger.info("Qt平台插件路径: %s", platforms_path)
        if "QT_PLUGIN_PATH" not in os.environ and os.path.exists(plugins_path):
            os.environ["QT_PLUGIN_PATH"] = plugins_path
            logger.info("Qt插件路径: %s", plugins_path)
    except Exception as exc:
        logger.warning("警告: 无法自动设置Qt插件路径: %s", exc)


class QtApplicationRuntime(ApplicationRuntime):
    """Own Qt-specific event-loop and application lifecycle operations."""

    def create_application(
        self,
        logger: object,
        argv: list[str] | None = None,
    ) -> QApplication:
        _ensure_qt_plugin_paths(logger)

        dont_use_native_menus_attr = getattr(Qt, "AA_DontUseNativeMenuWindows", None)
        if dont_use_native_menus_attr is not None:
            QApplication.setAttribute(dont_use_native_menus_attr, True)

        app = QApplication(sys.argv if argv is None else argv)
        app.setQuitOnLastWindowClosed(False)
        return app

    def connect_exit_acknowledged(
        self,
        application: object,
        callback: Callable[[], None],
    ) -> None:
        application.aboutToQuit.connect(callback)

    def schedule_once(self, delay_ms: int, callback: Callable[[], None]) -> None:
        QTimer.singleShot(max(0, int(delay_ms)), callback)

    def run_event_loop(self, application: object) -> int:
        return int(application.exec_())

    def process_events(self, application: object) -> None:
        application.processEvents()

    def request_exit(self, application: object, exit_code: int) -> None:
        application.sendPostedEvents(None, QEvent.DeferredDelete)
        application.exit(int(exit_code))

    def close_all_windows(self, application: object) -> None:
        application.closeAllWindows()


def create_qt_application(logger: object, argv: list[str] | None = None) -> QApplication:
    """Compatibility function for the previous script-level Qt runtime entry."""
    return QtApplicationRuntime().create_application(logger, argv)
