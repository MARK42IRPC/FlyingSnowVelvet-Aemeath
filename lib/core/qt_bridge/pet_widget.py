"""Qt QWidget event host for the desktop pet callbacks."""
from __future__ import annotations

from lib.core.graphics.types import Point, Rect, coerce_point
from lib.core.qt_bridge.entity_widget import QtEntityWidget
from lib.core.qt_bridge.input import keyboard_input_from_qt, mouse_input_from_qt
from lib.core.qt_bridge.widget_anchors import get_anchor_point as resolve_anchor_point
from lib.core.qt_bridge.window import render_draw_core


class QtPetWidget(QtEntityWidget):
    """Translate QWidget events and geometry into backend-neutral callbacks."""

    def get_position(self):
        """Return the legacy Qt position for remaining UI callers."""
        return self.frameGeometry().topLeft()

    def get_core_position(self) -> Point:
        geometry = self.frameGeometry()
        return Point(geometry.x(), geometry.y())

    def get_geometry(self):
        """Return the legacy Qt geometry for remaining UI callers."""
        return self.frameGeometry()

    def get_core_geometry(self) -> Rect:
        geometry = self.frameGeometry()
        return Rect(
            geometry.x(),
            geometry.y(),
            geometry.width(),
            geometry.height(),
        )

    def get_anchor_point(self, anchor_id: str):
        return resolve_anchor_point(self, anchor_id)

    def paintEvent(self, event):
        draw_core = self.prepare_render()
        if draw_core is not None:
            render_draw_core(self, draw_core)

    def enterEvent(self, event):
        self.handle_pointer_enter()

    def leaveEvent(self, event):
        self.handle_pointer_leave()

    def mousePressEvent(self, event):
        self.handle_pointer_press(mouse_input_from_qt(event, self))

    def mouseMoveEvent(self, event):
        self.handle_pointer_move(mouse_input_from_qt(event, self))

    def mouseReleaseEvent(self, event):
        self.handle_pointer_release(mouse_input_from_qt(event, self).button)

    def moveEvent(self, event):
        super().moveEvent(event)
        self.handle_window_moved(coerce_point(event.pos()) or Point())

    def keyPressEvent(self, event):
        self.handle_key_press(keyboard_input_from_qt(event, self))

    def keyReleaseEvent(self, event):
        self.handle_key_release(keyboard_input_from_qt(event, self))

    def closeEvent(self, event):
        self.handle_host_close()
        super().closeEvent(event)
