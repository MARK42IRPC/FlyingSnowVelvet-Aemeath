"""Qt implementation of the workbench floating-window host shell."""
from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QEvent, QObject, QPoint, Qt
from PyQt5.QtWidgets import QToolButton, QWidget

from lib.core.event.center import EventType, get_event_center
from lib.core.render.backends.qt.widgets.window_buttons import create_window_button
from lib.core.render.visuals.workbench_chrome import (
    FLOATING_WINDOW_OBJECT_NAME,
    floating_window_stylesheet,
    is_workbench_theme_change,
)


class FloatingWindowThemeWatcher:
    """Keep one floating window's stylesheet in sync with the workbench theme.

    The watcher only holds the event-center subscription while the window is
    visible, so hidden dialogs cannot be kept alive by the listener table.
    """

    def __init__(self, window: QWidget, refresh: Callable[[], None]) -> None:
        self._window = window
        self._refresh = refresh
        self._center = get_event_center()
        self._subscribed = False

    def subscribe(self) -> None:
        if self._subscribed:
            return
        self._subscribed = True
        self._center.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)

    def unsubscribe(self) -> None:
        if not self._subscribed:
            return
        self._subscribed = False
        try:
            self._center.unsubscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        except Exception:
            pass

    def _on_config_updated(self, event) -> None:
        if not is_workbench_theme_change(event):
            return
        try:
            self._refresh()
        except RuntimeError:
            # The underlying C++ widget is already gone; drop the subscription.
            self.unsubscribe()


class FloatingDragFilter(QObject):
    """Drag a frameless window by its title widgets."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self._handles: list[QWidget] = []
        self._cursor_handles: list[QWidget] = []
        self._offset = QPoint()
        self._dragging = False

    def attach(self, *widgets: QWidget, cursor: bool = True) -> None:
        for widget in widgets:
            if widget is None or widget in self._handles:
                continue
            widget.installEventFilter(self)
            if cursor:
                widget.setCursor(Qt.OpenHandCursor)
                self._cursor_handles.append(widget)
            self._handles.append(widget)

    def detach(self) -> None:
        for widget in self._handles:
            widget.removeEventFilter(self)
        self._handles.clear()
        self._cursor_handles.clear()
        self._dragging = False

    def _set_cursor(self, cursor) -> None:
        for widget in self._cursor_handles:
            widget.setCursor(cursor)

    def eventFilter(self, watched, event) -> bool:
        if watched in self._handles:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._dragging = True
                self._offset = event.globalPos() - self._window.frameGeometry().topLeft()
                self._set_cursor(Qt.ClosedHandCursor)
                event.accept()
                return True
            if (
                event.type() == QEvent.MouseMove
                and self._dragging
                and (event.buttons() & Qt.LeftButton)
            ):
                self._window.move(event.globalPos() - self._offset)
                event.accept()
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._dragging = False
                self._set_cursor(Qt.OpenHandCursor)
                event.accept()
                return True
        return super().eventFilter(watched, event)


class QtWorkbenchFloatingWindow(QWidget):
    """Frameless floating window sharing the workbench theme and chrome."""

    #: Subclasses whose controller already refreshes the theme set this False.
    follows_workbench_theme = True

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(FLOATING_WINDOW_OBJECT_NAME)
        self._floating_drag = FloatingDragFilter(self)
        self._floating_theme = FloatingWindowThemeWatcher(self, self.refresh_floating_theme)
        self._floating_chrome_ready = False

    # -- theme ----------------------------------------------------------
    def floating_stylesheet(self) -> str:
        """Return the QSS applied to this window; subclasses may extend it."""
        return floating_window_stylesheet()

    def refresh_floating_theme(self) -> None:
        self.setStyleSheet(self.floating_stylesheet())
        self.update()

    def install_floating_chrome(self) -> None:
        """Apply the shared stylesheet once, after the layout exists."""
        if self._floating_chrome_ready:
            return
        self._floating_chrome_ready = True
        self.refresh_floating_theme()

    # -- host window ability -------------------------------------------
    def attach_floating_drag_handle(self, *widgets: QWidget, cursor: bool = True) -> None:
        """Let the given title widgets move the frameless window."""
        self._floating_drag.attach(*widgets, cursor=cursor)

    def create_floating_window_button(
        self,
        parent: QWidget,
        standard_icon,
        tooltip: str,
        callback: Callable[[], None],
        *,
        danger: bool = False,
    ) -> QToolButton:
        """Build a workbench window button (minimize/close) for this window."""
        return create_window_button(parent, standard_icon, tooltip, callback, danger=danger)

    def minimize_floating_window(self) -> None:
        """Collapse the panel back to the launcher (tray or workbench) entry."""
        hide_dialog = getattr(self, "hide_dialog", None)
        if callable(hide_dialog):
            hide_dialog()
        else:
            self.hide()

    def cleanup_floating_chrome(self) -> None:
        """Detach chrome resources; safe to call more than once."""
        self._floating_drag.detach()
        self._floating_theme.unsubscribe()

    # -- Qt events -----------------------------------------------------
    def showEvent(self, event) -> None:
        self.refresh_floating_theme()
        if self.follows_workbench_theme:
            self._floating_theme.subscribe()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        if self.follows_workbench_theme:
            self._floating_theme.unsubscribe()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        if self.follows_workbench_theme:
            self._floating_theme.unsubscribe()
        super().closeEvent(event)

    def deleteLater(self) -> None:
        if self.follows_workbench_theme:
            self._floating_theme.unsubscribe()
        super().deleteLater()


__all__ = [
    "FloatingDragFilter",
    "FloatingWindowThemeWatcher",
    "QtWorkbenchFloatingWindow",
]
