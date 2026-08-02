"""Qt event pump implementation."""
from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal

from lib.core.event.pump import EventPump


class QtEventPump(QObject):
    """Deliver callbacks through the Qt event loop thread."""

    trigger = pyqtSignal()

    def __init__(self, callback):
        super().__init__()
        self.trigger.connect(callback)

    def emit(self) -> None:
        self.trigger.emit()

    def disconnect(self) -> None:
        try:
            self.trigger.disconnect()
        except (TypeError, RuntimeError):
            pass


def create_event_pump(callback) -> EventPump:
    return QtEventPump(callback)
