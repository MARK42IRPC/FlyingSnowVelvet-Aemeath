"""Qt composition of the backend-neutral desktop pet controller."""

from lib.core.graphics.types import Point
from lib.core.pet_window import PetWindow
from lib.core.qt_bridge.input import get_cursor_position
from lib.core.qt_bridge.pet_widget import QtPetWidget
from lib.core.qt_bridge.pet_window_ui import (
    attach_pet_window_ui,
    preload_pet_window_ui,
    shutdown_pet_window_ui,
)
from lib.core.qt_bridge.scheduler import QtScheduler
from lib.core.qt_bridge.widget_anchors import publish_widget_anchor_response
from lib.core.qt_bridge.window import move_widget, set_pet_window_clickthrough
from lib.core.qt_bridge.window_setup import (
    finalize_pet_window_startup,
    setup_pet_window,
)


class QtPetWindow(PetWindow, QtPetWidget):
    """Combine the pure pet controller with the QWidget event host."""

    def _host_create_scheduler(self):
        return QtScheduler(parent=self)

    def _host_cursor_position(self) -> Point:
        return get_cursor_position()

    def _host_setup(self, on_close) -> None:
        setup_pet_window(self)
        attach_pet_window_ui(self, on_close=on_close)

    def _host_finalize_startup(self) -> None:
        finalize_pet_window_startup(self)

    def _preload_ui(self) -> None:
        preload_pet_window_ui(self)

    def _host_publish_anchor_response(self, **kwargs) -> None:
        publish_widget_anchor_response(self._event_center, self, **kwargs)

    def _host_set_clickthrough(self, enabled: bool) -> None:
        set_pet_window_clickthrough(self, enabled)

    def _host_toggle_command_dialog(self) -> None:
        self._cmd.toggle(self)

    def _host_move(self, position: Point) -> None:
        move_widget(self, position)

    def _host_request_repaint(self) -> None:
        self.update()

    def _host_shutdown_ui(self) -> None:
        shutdown_pet_window_ui(self)

    def shutdown_host(self) -> None:
        """Stop core state and destroy the QWidget at the Qt boundary."""
        self.cleanup_core_state()
        try:
            self.close()
        except Exception:
            pass
        try:
            self.deleteLater()
        except Exception:
            pass
