"""Workbench-style chrome shared by the standalone floating dialogs.

二维码登录、更新、语音包下载与公告都是无边框 ``Qt.Tool`` 浮窗。工具窗口没有
任务栏入口，原生最小化会让窗口失去恢复入口；因此这些浮窗统一遵守同一套宿主
外壳：工作台 token 样式表、可拖动的标题区，以及把窗口收回启动入口（托盘或
工作台）的最小化行为。产品像素仍来自 ``lib/core/graphics`` 的共享视觉层，本
模块只提供宿主窗口能力。
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QEvent, QObject, QPoint, Qt
from PyQt5.QtWidgets import QToolButton, QWidget

from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.core.event.center import EventType, get_event_center
from lib.script.ui.workbench_components import create_window_button
from lib.script.workbench.theme import get_workbench_colors, window_button_stylesheet

FLOATING_WINDOW_OBJECT_NAME = "WorkbenchFloatingWindow"


def floating_window_stylesheet() -> str:
    """Return the shared QSS for compact floating dialogs in the live theme."""
    c = get_workbench_colors()
    font_family = get_ui_font_family().replace("'", "\\'")
    border = scale_px(1, min_abs=1)
    radius = scale_px(4, min_abs=3)
    control_height = scale_px(32, min_abs=28)
    return f"""
    QWidget#{FLOATING_WINDOW_OBJECT_NAME},
    QWidget#{FLOATING_WINDOW_OBJECT_NAME} * {{
        font-family: '{font_family}';
    }}
    QLabel {{
        background: transparent;
        color: {c.text};
    }}
    QLabel#WorkbenchFloatingTitle {{
        color: {c.text};
        font-weight: 700;
    }}
    QLabel#WorkbenchFloatingSubtitle, QLabel#WorkbenchFloatingMeta {{
        color: {c.text_muted};
    }}
    QPushButton {{
        background: {c.surface_raised};
        color: {c.text};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
        min-height: {control_height}px;
        padding: 0px {scale_px(12, min_abs=10)}px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {c.surface_hover};
        border-color: {c.cyan};
    }}
    QPushButton:pressed {{
        background: {c.border};
    }}
    QPushButton:disabled {{
        background: {c.surface};
        color: {c.text_dim};
        border-color: {c.border};
    }}
    QPushButton#WorkbenchFloatingPrimary {{
        background: {c.pink};
        border-color: {c.pink};
        color: {c.canvas};
        font-weight: 700;
    }}
    QPushButton#WorkbenchFloatingPrimary:hover {{
        background: {c.pink_hover};
        border-color: {c.pink_hover};
    }}
    QPushButton#WorkbenchFloatingAccent {{
        background: {c.cyan};
        border-color: {c.cyan};
        color: {c.canvas};
        font-weight: 700;
    }}
    QPushButton#WorkbenchFloatingAccent:hover {{
        background: {c.pink_hover};
        border-color: {c.pink_hover};
    }}
    QProgressBar {{
        background: {c.surface_raised};
        color: {c.text};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {c.cyan};
        border-radius: {radius}px;
    }}
    QComboBox {{
        background: {c.surface_raised};
        color: {c.text};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
        min-height: {control_height}px;
        padding: 0px {scale_px(30, min_abs=26)}px 0px {scale_px(9, min_abs=7)}px;
    }}
    QComboBox:hover {{
        background: {c.surface_hover};
        border-color: {c.cyan};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: {scale_px(26, min_abs=22)}px;
        background: transparent;
        border: none;
    }}
    QComboBox::down-arrow {{
        image: url(resc/ui/combo_down_arrow.svg);
        width: {scale_px(12, min_abs=10)}px;
        height: {scale_px(8, min_abs=6)}px;
    }}
    QComboBox QAbstractItemView {{
        background: {c.surface};
        color: {c.text};
        border: {border}px solid {c.border_strong};
        outline: none;
        selection-background-color: {c.surface_hover};
        selection-color: {c.text};
    }}
    QTextBrowser, QTextEdit {{
        background: {c.surface};
        color: {c.text};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
    }}
    QScrollBar:vertical {{
        background: {c.canvas};
        width: {scale_px(10, min_abs=8)}px;
        border: none;
    }}
    QScrollBar::handle:vertical {{
        background: {c.border_strong};
        min-height: {scale_px(28, min_abs=22)}px;
        border-radius: {scale_px(3, min_abs=2)}px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c.text_dim};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
        border: none;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    {window_button_stylesheet()}
    """


def is_workbench_theme_change(event: object) -> bool:
    """Return True when a CONFIG_UPDATED payload switched the workbench theme."""
    data = getattr(event, "data", None)
    if not isinstance(data, dict):
        return False
    values = data.get("values")
    if not isinstance(values, dict):
        return False
    ui_values = values.get("UI")
    return isinstance(ui_values, dict) and "workbench_light_theme" in ui_values


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

    def _set_cursor(self, cursor: Qt.CursorShape) -> None:
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


class WorkbenchFloatingWindow(QWidget):
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
    "FLOATING_WINDOW_OBJECT_NAME",
    "FloatingDragFilter",
    "FloatingWindowThemeWatcher",
    "WorkbenchFloatingWindow",
    "floating_window_stylesheet",
    "is_workbench_theme_change",
]
